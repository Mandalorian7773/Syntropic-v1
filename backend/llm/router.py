"""Task router. Owner: person 3. Empty by design.

Will contain: the classifier that maps an incoming request to a TaskType and a
model id, reading the registry from config/models.yaml and trained on
config/router_trainset.jsonl. Emits router.decision with confidence, a
human-readable reason and the alternatives it rejected.

Adding a model must be an edit to config/models.yaml plus a weight file --
no code change here.
"""
