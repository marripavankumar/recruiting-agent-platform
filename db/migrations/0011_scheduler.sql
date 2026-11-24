-- 0011: built-in scheduler (plan sections 11 and 12). One row per job; a run is claimed by an
-- atomic UPDATE, so several scheduler replicas never run a job twice in one interval.

CREATE TABLE scheduler_job (
    name             text PRIMARY KEY,
    interval_seconds integer NOT NULL CHECK (interval_seconds > 0),
    last_started_at  timestamptz,
    last_finished_at timestamptz,
    last_status      text CHECK (last_status IN ('running', 'ok', 'failed')),
    last_error       text,
    last_result      jsonb NOT NULL DEFAULT '{}'
);
