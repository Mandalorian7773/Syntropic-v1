# Demo run sheet — 8 minutes

Owner: person 2 (with person 1 driving the UI, person 3 on the machine).

Rehearse this end to end at least three times on the demo host itself. The
laptop that has never run the demo is the laptop that fails the demo.

**Hard rule:** the network status indicator stays visible on screen the entire
time. It is the claim we are making; it should never leave the judges' view.

---

## 0. Setup (before the clock starts)

- [ ] `make demo` — all services up
- [ ] `make airgap` — passes, screenshot kept as backup evidence
- [ ] Wi-Fi off, ethernet unplugged, physically visible
- [ ] Demo corpus already ingested; do not ingest live
- [ ] Browser at 100% zoom, one tab, notifications off

---

## 1. Air-gapped operation — 1 min

*Show that nothing leaves the machine.*

- Point at the network status panel: external packets 0, DNS queries 0.
- Unplug / show Wi-Fi is already off.
- Ask a question anyway. It answers.

**Say:** …

---

## 2. Multimodal document understanding — 2 min

*Show a scanned refinery SOP being read, not just matched.*

- Upload / open a scanned page with a table.
- Ask a question answerable only from that table.
- Land on the citation: filename, page number, snippet.

**Say:** …

---

## 3. Agentic tool use with sandboxed execution — 2 min

*Show the agent choosing a tool and running real code, safely.*

- Ask something requiring computation over uploaded data.
- Narrate the visible `agent.step` / `tool.call` / `tool.result` sequence.
- Show the sandbox has no network and cannot see the host filesystem.

**Say:** …

---

## 4. Model routing on constrained hardware — 1.5 min

*Show one 8 GB GPU serving two models without falling over.*

- Ask a coding question; the router decision appears with its reason.
- The model swap is visible: `model.loading` with `evicting`, then `model.ready`.
- Name the number: 8 GB, one model resident, ~8 s swap, deliberate.

**Say:** …

---

## 5. Artifact generation — 1.5 min

*Show a real deliverable, not a chat log.*

- Ask for an approval note / inspection report.
- Download the .docx. **Open it on screen.** A download that is never opened
  proves nothing.

**Say:** …

---

## Close — 30 s

Sovereign, on-premise, open weights, one laptop, zero packets. Numbers from
`bench/results/`, not adjectives.

---

## If it breaks

| Failure | Response |
|---|---|
| Model will not load | Restart llama-server, keep talking over it |
| Retrieval returns nothing | Fall back to the pre-ingested question that always works |
| Sandbox timeout | Show the `error` event handling — recoverable, by design |
| Total stack failure | Recorded backup walkthrough, kept on the same laptop |
