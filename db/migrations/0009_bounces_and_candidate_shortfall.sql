-- 0009: Phase 8 exception paths (plan section 14).
-- S6: a hard bounce marks the contact's email invalid so Agent 3 skips it and suggests the
-- next-best contact. The delivery log already allows 'bounced' (migration 0003).
-- S12: too few qualified candidates opens an Agent 12 task for the recruiter; a widened search
-- is recorded on the ranking.

ALTER TABLE company_contact
    ADD COLUMN email_valid boolean NOT NULL DEFAULT true;

ALTER TABLE candidate_ranking
    ADD COLUMN widened jsonb;

ALTER TABLE follow_up DROP CONSTRAINT IF EXISTS follow_up_kind_check;
ALTER TABLE follow_up ADD CONSTRAINT follow_up_kind_check
    CHECK (kind IN ('client_outreach', 'submission_feedback', 'candidate_outreach',
                    'candidate_details', 'interview_scheduling', 'interview_prep',
                    'interview_feedback', 'offer', 'start_date', 'candidate_shortfall'));
