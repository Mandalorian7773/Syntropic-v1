"""Chunking. Owner: person 2. Empty by design.

Will contain: the split strategy and the metadata every chunk carries
(doc_id, filename, page) -- exactly the fields the `citation` event needs, so
a hit can be cited without a second lookup.
"""
