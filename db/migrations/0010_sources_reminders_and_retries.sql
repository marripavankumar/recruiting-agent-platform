-- 0010: remaining Phase 8 exception paths (plan section 14).
-- S3: a company found by several sources keeps every source.
-- S9: a "JD to follow" reply is remembered so Agent 12 can remind the BD Manager.
-- S24: deliveries the provider did not take are retried with back-off under the same approval.

ALTER TABLE company
    ADD COLUMN sources        text[] NOT NULL DEFAULT '{}',
    ADD COLUMN jd_promised_at timestamptz;
UPDATE company SET sources = ARRAY[source] WHERE sources = '{}';

ALTER TABLE outreach_message DROP CONSTRAINT outreach_message_delivery_status_check;
ALTER TABLE outreach_message ADD CONSTRAINT outreach_message_delivery_status_check
    CHECK (delivery_status IN ('sending', 'sent', 'queued_for_manual_send', 'manually_sent',
                               'bounced', 'retry_scheduled', 'failed'));
ALTER TABLE outreach_message
    ADD COLUMN attempts        integer NOT NULL DEFAULT 0,
    ADD COLUMN next_attempt_at timestamptz;
CREATE INDEX outreach_message_retry_idx ON outreach_message (next_attempt_at)
    WHERE delivery_status = 'retry_scheduled';

ALTER TABLE follow_up DROP CONSTRAINT IF EXISTS follow_up_kind_check;
ALTER TABLE follow_up ADD CONSTRAINT follow_up_kind_check
    CHECK (kind IN ('client_outreach', 'submission_feedback', 'candidate_outreach',
                    'candidate_details', 'interview_scheduling', 'interview_prep',
                    'interview_feedback', 'offer', 'start_date', 'candidate_shortfall',
                    'jd_promised'));
