-- 0006: Agents 8-9 (plan section 9): outreach and resume collection per job and candidate.

CREATE TABLE candidate_engagement (
    id                uuid PRIMARY KEY,
    job_id            uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    candidate_id      uuid NOT NULL REFERENCES candidate (id) ON DELETE CASCADE,
    status            text NOT NULL
        CHECK (status IN ('drafted', 'draft_rejected', 'contacted', 'accepted', 'replied',
                          'interested', 'not_interested', 'do_not_contact', 'details_requested',
                          'complete')),
    approvals         jsonb NOT NULL DEFAULT '{}',  -- kind -> approval_request id
    token_hash        char(64) UNIQUE,              -- upload link; the token itself is not stored
    token_expires_at  timestamptz,
    requested_at      timestamptz,
    consent_store_at  timestamptz,
    consent_submit_at timestamptz,
    consent_evidence  text,
    missing           text[] NOT NULL DEFAULT '{}',
    next_reminder_at  timestamptz,                  -- Agent 12 picks these up
    history           jsonb NOT NULL DEFAULT '[]',
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, candidate_id)
);
CREATE INDEX candidate_engagement_candidate_idx ON candidate_engagement (candidate_id);
CREATE INDEX candidate_engagement_reminder_idx ON candidate_engagement (next_reminder_at)
    WHERE next_reminder_at IS NOT NULL;
CREATE INDEX candidate_engagement_approvals_idx ON candidate_engagement USING gin (approvals);
