-- 0005: Agents 6-7 (plan section 8): richer candidate profiles, rankings and recruiter feedback.

ALTER TABLE candidate
    ADD COLUMN education     text,
    ADD COLUMN industries    text[] NOT NULL DEFAULT '{}',
    ADD COLUMN work_history  jsonb  NOT NULL DEFAULT '[]',
    ADD COLUMN communication jsonb  NOT NULL DEFAULT '{}';
CREATE INDEX candidate_email_idx ON candidate (lower(email));
CREATE INDEX candidate_skills_idx ON candidate USING gin (skills);

-- One current ranking per job; re-running replaces it but keeps recruiter verdicts.
CREATE TABLE candidate_ranking (
    job_id    uuid PRIMARY KEY REFERENCES job (id) ON DELETE CASCADE,
    ranked_at timestamptz NOT NULL,
    weights   jsonb NOT NULL,
    searched  integer NOT NULL DEFAULT 0
);

CREATE TABLE candidate_ranking_entry (
    job_id          uuid NOT NULL REFERENCES candidate_ranking (job_id) ON DELETE CASCADE,
    candidate_id    uuid NOT NULL REFERENCES candidate (id) ON DELETE CASCADE,
    score           smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
    components      jsonb NOT NULL,
    strengths       jsonb NOT NULL DEFAULT '[]',
    gaps            jsonb NOT NULL DEFAULT '[]',
    rank            smallint,  -- NULL: excluded by a hard filter
    excluded_reason text,
    verdict         text CHECK (verdict IN ('qualified', 'not_qualified', 'excluded')),
    note            text,
    reviewed_by     text,
    reviewed_at     timestamptz,
    PRIMARY KEY (job_id, candidate_id)
);

-- Administrator-set ranking weights (single row).
CREATE TABLE ranking_settings (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    weights    jsonb NOT NULL,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
