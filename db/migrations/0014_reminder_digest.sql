-- 0014: daily reminder digest for staff (Agent 12, plan section 11). One row per user and day,
-- inserted before the email is sent, so scheduler replicas and restarts never send it twice.
-- A failed send deletes its row and the next run retries.

CREATE TABLE reminder_digest (
    user_id     uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    digest_date date NOT NULL,  -- the day in RAP_DIGEST_TIMEZONE
    sent_at     timestamptz NOT NULL,
    PRIMARY KEY (user_id, digest_date)
);
