"""Task router. Owner: person 3. Required demonstration #1.

Not a keyword matcher. Two real classifiers over config/router_trainset.jsonl
(200 prompts, 40 per class, stratified 160/40 split, held-out accuracy written
to data/router_metrics.json -- acceptance criterion 2):

  primary   LogisticRegression over Person 2's embedding endpoint
            (POST {RAG_ENDPOINT}/embed). Trained at startup if the endpoint
            answers; never loads an embedding model of its own.
  fallback  LogisticRegression over a TF-IDF pipeline, trained locally so the
            router still classifies when ragsvc is down mid-dev.

Decision order, strict:
  1. Image attachment -> vision capability is mandatory. Hard rule, no model.
  2. Otherwise classify the prompt.
  3. Confidence < min_confidence -> the model marked default, and the reason
     says so.
  4. Among models with the capability, lowest vram_mb wins.
  5. Switching away from the resident model costs ~8 s, so the challenger must
     clear min_confidence + switch_penalty or the incumbent (if capable) keeps
     the slot. Both thresholds live in config/models.yaml, not here.

Adding a model in config/models.yaml requires zero edits in this file. That is
the acceptance test for this module's design.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from contracts import Attachment, RouterDecision
from llm.manager import ModelRegistry

TASK_CAPABILITY = {"general": "general", "code": "code", "document": "document",
                   "vision": "vision", "data": "data"}


class Router:
    def __init__(self, registry: ModelRegistry, rag_endpoint: str,
                 trainset_path: str, data_dir: str) -> None:
        self._registry = registry
        self._rag = rag_endpoint.rstrip("/")
        self._trainset_path = Path(trainset_path)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._tfidf: Pipeline | None = None
        self._embed_clf: LogisticRegression | None = None
        self.metrics: dict = {}

    # --- training -------------------------------------------------------------

    def _load_trainset(self) -> tuple[list[str], list[str]]:
        texts, labels = [], []
        for line in self._trainset_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            texts.append(row["text"])
            labels.append(row["task_type"])
        return texts, labels

    def _embed(self, texts: list[str], timeout: float = 30.0) -> np.ndarray | None:
        try:
            resp = httpx.post(f"{self._rag}/embed", json={"texts": texts},
                              timeout=timeout)
            resp.raise_for_status()
            return np.asarray(resp.json()["vectors"], dtype=np.float32)
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    def prepare(self) -> dict:
        """Train (or reload) both classifiers. Called once at startup; cheap
        enough (<2 s for TF-IDF) that retraining beats cache-invalidation bugs."""
        texts, labels = self._load_trainset()
        x_train, x_test, y_train, y_test = train_test_split(
            texts, labels, test_size=40, stratify=labels, random_state=42
        )

        self._tfidf = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True,
                                      min_df=1, lowercase=True)),
            ("clf", LogisticRegression(max_iter=2000, C=4.0)),
        ])
        self._tfidf.fit(x_train, y_train)
        preds = self._tfidf.predict(x_test)
        tfidf_acc = accuracy_score(y_test, preds)
        joblib.dump(self._tfidf, self._data_dir / "router_tfidf.pkl")

        # The number that actually costs anything: did the label confusion
        # change which MODEL would serve the prompt? document<->general
        # confusion is free (same model); code<->anything is not.
        def model_of(task: str) -> str:
            capable = self._registry.with_capability(TASK_CAPABILITY[task])
            return min(capable, key=lambda m: m.vram_mb).id if capable \
                else self._registry.default.id

        model_acc = sum(model_of(a) == model_of(b)
                        for a, b in zip(y_test, preds)) / len(y_test)

        embed_acc = None
        vec_train = self._embed(x_train)
        vec_test = self._embed(x_test) if vec_train is not None else None
        if vec_train is not None and vec_test is not None:
            self._embed_clf = LogisticRegression(max_iter=2000)
            self._embed_clf.fit(vec_train, y_train)
            embed_acc = accuracy_score(y_test, self._embed_clf.predict(vec_test))
            joblib.dump(self._embed_clf, self._data_dir / "router_embed.pkl")

        self.metrics = {
            "train_size": len(x_train), "test_size": len(x_test),
            "tfidf_accuracy": round(float(tfidf_acc), 4),
            "model_choice_accuracy": round(float(model_acc), 4),
            "embed_accuracy": round(float(embed_acc), 4) if embed_acc is not None else None,
            "classes": sorted(set(labels)),
        }
        (self._data_dir / "router_metrics.json").write_text(
            json.dumps(self.metrics, indent=2), encoding="utf-8"
        )
        return self.metrics

    # --- classification -------------------------------------------------------

    def _classify(self, prompt: str) -> tuple[str, float, str]:
        """Returns (task_type, confidence, which classifier answered)."""
        if self._embed_clf is not None:
            vec = self._embed([prompt], timeout=5.0)
            if vec is not None:
                probs = self._embed_clf.predict_proba(vec)[0]
                idx = int(np.argmax(probs))
                return self._embed_clf.classes_[idx], float(probs[idx]), "embeddings"
        if self._tfidf is None:
            raise RuntimeError("router not prepared; call prepare() at startup")
        probs = self._tfidf.predict_proba([prompt])[0]
        idx = int(np.argmax(probs))
        return self._tfidf.named_steps["clf"].classes_[idx], float(probs[idx]), "tf-idf"

    def decide(self, prompt: str, attachments: list[Attachment],
               loaded_id: str | None) -> RouterDecision:
        cfg = self._registry.router
        default = self._registry.default

        if any(a.mime.startswith("image/") for a in attachments):
            task, confidence, reason = "vision", 1.0, (
                "image attachment present; vision capability is mandatory (hard rule)"
            )
        else:
            task, confidence, via = self._classify(prompt)
            if task == "general" and attachments:
                task = "document"
                reason = f"classified general via {via} but a document is attached"
            else:
                reason = f"classified as {task} via {via} classifier"
            if confidence < cfg.min_confidence:
                candidates = [m.id for m in
                              self._registry.with_capability(TASK_CAPABILITY[task])]
                return RouterDecision(
                    model_id=default.id, task_type=task, confidence=confidence,
                    reason=f"confidence {confidence:.2f} below "
                           f"{cfg.min_confidence:.2f}; falling back to default model",
                    alternatives=[c for c in candidates if c != default.id],
                )

        capability = TASK_CAPABILITY[task]
        capable = sorted(self._registry.with_capability(capability),
                         key=lambda m: m.vram_mb)
        if not capable:
            return RouterDecision(
                model_id=default.id, task_type=task, confidence=confidence,
                reason=f"no model advertises {capability!r}; using default",
                alternatives=[],
            )

        chosen = capable[0]
        alternatives = [m.id for m in capable[1:]]
        incumbent_capable = loaded_id is not None and any(
            m.id == loaded_id for m in capable
        )
        if (incumbent_capable and chosen.id != loaded_id
                and confidence < cfg.min_confidence + cfg.switch_penalty):
            alternatives = [m.id for m in capable if m.id != loaded_id]
            chosen = self._registry.get(loaded_id)  # keep the resident model
            reason += (f"; resident model kept -- switching costs a reload and "
                       f"confidence {confidence:.2f} does not clear "
                       f"{cfg.min_confidence + cfg.switch_penalty:.2f}")
        elif chosen.id != (loaded_id or ""):
            reason += f"; lowest-VRAM model with {capability!r} capability"

        return RouterDecision(
            model_id=chosen.id, task_type=task, confidence=round(confidence, 4),
            reason=reason, alternatives=alternatives,
        )
