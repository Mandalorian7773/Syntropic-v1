-- SQLite schema. Owner: person 3. Placeholder.
--
-- Will hold: sessions, messages, tool_calls, documents, artifacts and the
-- append-only audit log. SQLite because the deployment target is one laptop
-- and a second server process is a second thing that can fail on stage.

PRAGMA journal_mode = WAL;

-- CREATE TABLE sessions (...);
-- CREATE TABLE messages (...);
-- CREATE TABLE tool_calls (...);
-- CREATE TABLE artifacts (...);
-- CREATE TABLE audit_log (...);
