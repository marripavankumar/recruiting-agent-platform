-- 0007: Agents 10-11 (plan section 10): match reports, recruiter approval and client submissions.

ALTER TABLE candidate_engagement DROP CONSTRAINT IF EXISTS candidate_engagement_status_check;
ALTER TABLE candidate_engagement ADD CONSTRAINT candidate_engagement_status_check
    CHECK (status IN ('drafted', 'draft_rejected', 'contacted', 'accepted', 'replied',
                      'interested', 'not_interested', 'do_not_contact', 'details_requested',
                      'complete', 'submitted'));

CREATE TABLE client_submission (
    id                    uuid PRIMARY KEY,
    job_id                uuid NOT NULL REFERENCES job (id) ON DELETE CASCADE,
    candidate_id          uuid NOT NULL REFERENCES candidate (id) ON DELETE CASCADE,
    client_company_id     uuid NOT NULL REFERENCES company (id),
    status                text NOT NULL
        CHECK (status IN ('matched', 'pending_approval', 'rejected', 'submitted', 'failed',
                          'withdrawn')),
    match                 jsonb NOT NULL DEFAULT '{}',  -- Agent 10 report
    package               jsonb NOT NULL DEFAULT '{}',  -- Agent 11: the nine BRD items
    recipient             text,
    approval_request_id   uuid UNIQUE REFERENCES approval_request (id),
    right_to_represent_at timestamptz,
    right_to_represent_by text,
    warnings              text[] NOT NULL DEFAULT '{}',
    rejection_reason      text,                         -- kept for model tuning (S19)
    sent_at               timestamptz,
    sent_content          jsonb NOT NULL DEFAULT '{}',  -- exactly what was sent (audit)
    follow_up_at          timestamptz,                  -- Agent 12 picks these up
    client_decision       text CHECK (client_decision IN ('interview', 'hold', 'rejected')),
    client_feedback       text,
    history               jsonb NOT NULL DEFAULT '[]',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    -- A submission that was sent always has its approval, consent confirmation and sent copy.
    CHECK (status <> 'submitted'
           OR (approval_request_id IS NOT NULL AND right_to_represent_at IS NOT NULL
               AND sent_at IS NOT NULL))
);
CREATE INDEX client_submission_job_idx ON client_submission (job_id);
CREATE INDEX client_submission_candidate_client_idx
    ON client_submission (candidate_id, client_company_id);
CREATE INDEX client_submission_follow_up_idx ON client_submission (follow_up_at)
    WHERE follow_up_at IS NOT NULL;
-- S18: at most one live submission per candidate and client, enforced by the database too.
CREATE UNIQUE INDEX client_submission_live_uidx ON client_submission (candidate_id, client_company_id)
    WHERE status IN ('pending_approval', 'submitted');
