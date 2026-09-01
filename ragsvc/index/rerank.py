"""Cross-encoder rerank. Owner: person 2. Empty by design.

Will contain: the final reorder of the fused candidates. CPU, like embed.py.
Measure it against eval/retrieval_eval.py before keeping it -- a rerank that
costs 400 ms and moves nothing is 400 ms off the demo clock.
"""
