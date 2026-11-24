-- 0012: mailbox reader (plan sections 7, 9 and 14). One row per inbound message, keyed by its
-- Message-ID; a message is claimed atomically, so it is handled once across scheduler replicas.
-- Holds no message content, only what happened to it.

CREATE TABLE inbound_message (
    message_key text PRIMARY KEY,
    status      text NOT NULL CHECK (status IN ('processing', 'done', 'failed')),
    attempts    integer NOT NULL DEFAULT 1,
    claimed_at  timestamptz NOT NULL,
    finished_at timestamptz,
    outcome     text,
    error       text
);
CREATE INDEX inbound_message_failed_idx ON inbound_message (finished_at) WHERE status = 'failed';
