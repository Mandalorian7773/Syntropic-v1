"""SQLite data access. Owner: person 3. Empty by design.

Will contain: connection handling against DB_PATH, schema migration from
schema.sql on first start, and the session/message/artifact queries behind
GET /api/sessions and GET /api/sessions/{id}.
"""
