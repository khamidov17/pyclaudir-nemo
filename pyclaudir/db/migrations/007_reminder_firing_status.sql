-- Add a transient 'firing' status to reminders.
--
-- A reminder is moved pending -> firing the moment the loop claims it for
-- delivery, and only pending rows are fetched as "due". This stops a turn that
-- outlasts the poll interval (e.g. a delegated coding task) from being
-- re-fetched and delivered/executed repeatedly. on_success advances/closes the
-- row; on_failure (and a startup reclaim) returns it to pending.
--
-- SQLite cannot alter a CHECK constraint in place, so rebuild the table. No
-- other table references reminders, so this is a straight copy.

CREATE TABLE reminders_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    text          TEXT NOT NULL,
    trigger_at    TEXT NOT NULL,          -- UTC ISO8601
    cron_expr     TEXT,                   -- NULL for one-shot, cron string for recurring
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'firing', 'sent', 'cancelled')),
    created_at    TEXT NOT NULL,
    auto_seed_key TEXT
);

INSERT INTO reminders_new (
    id, chat_id, user_id, text, trigger_at, cron_expr, status, created_at, auto_seed_key
)
SELECT
    id, chat_id, user_id, text, trigger_at, cron_expr, status, created_at, auto_seed_key
FROM reminders;

DROP TABLE reminders;
ALTER TABLE reminders_new RENAME TO reminders;

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, trigger_at);
CREATE INDEX IF NOT EXISTS idx_reminders_auto_seed_key ON reminders(auto_seed_key);
