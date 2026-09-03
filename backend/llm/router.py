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


class ModelChoiceError(ValueError):
    """A user-chosen model cannot serve this turn.

    Raised instead of quietly routing somewhere else. A picker that silently
    ignores the pick is worse than one that refuses it: the user believes the
    answer came from the model they selected, and nothing on screen says
    otherwise.
    """

    def __init__(self, message: str, *, model_id: str, reason: str) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.reason = reason  # "unknown" | "incapable"


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
            # C=10, not the default 1.0. BGE-M3 vectors are unit-norm in 1024
            # dims, and with 160 training rows the default L2 penalty flattens
            # predict_proba to ~0.33 for the argmax even when the class is
            # right -- measured: held-out accuracy 0.925, yet every document
            # question fell under the 0.60 threshold and the panel read
            # "falling back to default model" through the whole demo. Weaker
            # regularisation sharpens the probabilities; accuracy is re-measured
            # on every startup and written to router_metrics.json regardless.
            self._embed_clf = LogisticRegression(max_iter=4000, C=10.0)
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

    def required_task(self, prompt: str, attachments: list[Attachment]) -> tuple[str, float]:
        """The task this turn needs, and how sure we are.

        Same two rules `decide` opens with, factored out so the override path
        judges a user's pick against exactly what the router would have judged
        it against. A second, parallel notion of "what kind of turn is this"
        would drift from this one within a week.
        """
        if any(a.mime.startswith("image/") for a in attachments):
            return "vision", 1.0
        task, confidence, _via = self._classify(prompt)
        if task == "general" and attachments:
            task = "document"
        return task, confidence

    def decide_override(self, model_id: str, prompt: str,
                        attachments: list[Attachment]) -> RouterDecision:
        """Honour a user's model choice, or refuse it with a reason.

        Refusal is deliberately narrow. The classifier is a guess with a
        confidence attached, and `decide` itself does not trust a label below
        `min_confidence` -- it falls back to the default model rather than
        acting on it. So an override is refused only where the router would
        also have treated the requirement as binding:

          * an image is attached and the model has no vision capability. The
            router calls this a hard rule and so is this: the model physically
            cannot see the attachment.
          * the classifier is at or above `min_confidence` that the turn needs
            a capability the model does not advertise.

        Below that threshold the user's explicit choice wins over a guess the
        router would not have acted on either. Anything stricter turns a
        picker into a suggestion box.
        """
        spec = self._registry.get(model_id)  # KeyError for an unknown id
        task, confidence = self.required_task(prompt, attachments)
        capability = TASK_CAPABILITY[task]
        binding = task == "vision" or confidence >= self._registry.router.min_confidence

        if binding and capability not in spec.capabilities:
            capable = sorted(self._registry.with_capability(capability),
                             key=lambda m: m.vram_mb)
            suggestion = (f" Try {', '.join(m.id for m in capable)}."
                          if capable else
                          f" No configured model advertises {capability!r}.")
            raise ModelChoiceError(
                f"{model_id!r} cannot handle this request: it needs the "
                f"{capability!r} capability and {model_id!r} advertises "
                f"{sorted(spec.capabilities) or 'none'}.{suggestion}",
                model_id=model_id, reason="incapable",
            )

        alternatives = [m.id for m in self._registry.with_capability(capability)
                        if m.id != model_id]
        return RouterDecision(
            model_id=model_id, task_type=task, confidence=1.0,
            reason=f"user selected {model_id}",
            alternatives=alternatives,
        )

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
