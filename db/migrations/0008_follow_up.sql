-- 0008: Agent 12 (plan section 11): follow-ups, cadence (Q13), placement pipeline, and
-- cancelling pending approvals when a job is closed (scenario S22).

ALTER TABLE approval_decision DROP CONSTRAINT IF EXISTS approval_decision_action_check;
ALTER TABLE approval_decision DROP CONSTRAINT IF EXISTS approval_decision_check;
ALTER TABLE approval_decision ADD CONSTRAINT approval_decision_action_check
    CHECK (action IN ('approve', 'edit_and_approve', 'reject', 'cancel'));
ALTER TABLE approval_decision ADD CONSTRAINT approval_decision_check
    CHECK (action NOT IN ('reject', 'cancel') OR reason IS NOT NULL);

ALTER TABLE client_submission
    ADD COLUMN stage text CHECK (stage IN ('submitted', 'interview_requested',
        'interview_scheduled', 'interviewed', 'offer_extended', 'offer_accepted', 'started',
        'client_rejected', 'offer_declined', 'candidate_withdrawn')),
    ADD COLUMN stage_at     timestamptz,
    ADD COLUMN interview_at timestamptz,
    ADD COLUMN offer_at     timestamptz,
    ADD COLUMN start_date   date,
    ADD COLUMN stage_reason text;

CREATE TABLE follow_up (
    id           uuid PRIMARY KEY,
    kind         text NOT NULL
        CHECK (kind IN ('client_outreach', 'submission_feedback', 'candidate_outreach',
                        'candidate_details', 'interview_scheduling', 'interview_prep',
                        'interview_feedback', 'offer', 'start_date')),
    subject_id   text NOT NULL,          -- company, engagement or submission id
    owner_role   text NOT NULL,
    anchor_at    timestamptz NOT NULL,
    due_at       timestamptz,
    status       text NOT NULL CHECK (status IN ('open', 'done', 'stopped', 'cancelled')),
    attempt      integer NOT NULL DEFAULT 0,
    job_id       uuid REFERENCES job (id) ON DELETE CASCADE,
    candidate_id uuid REFERENCES candidate (id) ON DELETE CASCADE,
    company_id   uuid REFERENCES company (id) ON DELETE CASCADE,
    owner_id     text,
    note         text NOT NULL DEFAULT '',
    approvals    text[] NOT NULL DEFAULT '{}',
    context      jsonb NOT NULL DEFAULT '{}',
    history      jsonb NOT NULL DEFAULT '[]',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, subject_id),
    -- Phase 7 exit criterion: every open item has a next reminder date.
    CHECK (status <> 'open' OR due_at IS NOT NULL)
);
CREATE INDEX follow_up_open_due_idx ON follow_up (due_at) WHERE status = 'open';
CREATE INDEX follow_up_job_idx ON follow_up (job_id);

CREATE TABLE follow_up_cadence (
    kind       text PRIMARY KEY,
    days       integer[] NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
