-- 0004: fields and tables for Agents 1-5 (plan sections 6 and 7) and LLM cost tracking (5.4).

-- Agent 1: de-duplication by web domain, Agent 1 output fields, Agent 2 priority score.
ALTER TABLE company
    ADD COLUMN domain          text,
    ADD COLUMN country         char(2),
    ADD COLUMN website         text,
    ADD COLUMN linkedin_url    text,
    ADD COLUMN career_page_url text,
    ADD COLUMN priority_score  smallint NOT NULL DEFAULT 0 CHECK (priority_score BETWEEN 0 AND 100);
CREATE UNIQUE INDEX company_domain_idx ON company (domain);
CREATE INDEX company_priority_idx ON company (priority_score DESC);

ALTER TABLE company_contact
    ADD COLUMN role_category text NOT NULL DEFAULT 'other'
        CHECK (role_category IN ('hiring_manager', 'engineering_manager', 'hr_manager',
                                 'talent_acquisition', 'director_of_engineering', 'recruiter',
                                 'other')),
    ADD COLUMN phone text;
CREATE INDEX company_contact_email_idx ON company_contact (lower(email));

-- Agent 2: public postings that signal hiring (feeds Hiring History and the priority score).
CREATE TABLE job_posting (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id     uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    title          text NOT NULL,
    target_role    text,
    url            text NOT NULL UNIQUE,
    location       text,
    hiring_manager text,
    date_posted    date,
    first_seen     timestamptz,
    last_seen      timestamptz,
    active         boolean NOT NULL DEFAULT true
);
CREATE INDEX job_posting_company_idx ON job_posting (company_id) WHERE active;

-- Agents 4-5: fields for the Account Manager to confirm, and the search profile.
ALTER TABLE job
    ADD COLUMN review_fields text[] NOT NULL DEFAULT '{}',
    ADD COLUMN analysis      jsonb  NOT NULL DEFAULT '{}';

ALTER TABLE candidate ADD COLUMN resume_text text;

-- LLM gateway cost tracking per prompt template version.
CREATE TABLE llm_usage (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_key  text NOT NULL,
    provider      text NOT NULL,
    model         text NOT NULL,
    input_tokens  integer NOT NULL,
    output_tokens integer NOT NULL,
    cost_usd      numeric(12, 6),
    at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_usage_at_idx ON llm_usage (at);
