-- 0001: core schema (plan section 5.1, BRD section 8).
-- Company, Job and Candidate databases plus the approval, consent, opt-out and audit records
-- needed to prove BRD section 10 approvals and privacy compliance. Standard PostgreSQL 16 only,
-- so it runs on any cloud's managed PostgreSQL or on-premises.

CREATE TABLE company (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name             text NOT NULL,
    industry         text,
    revenue_usd      numeric(16, 2),
    employees        integer,
    locations        jsonb NOT NULL DEFAULT '[]',
    hiring_history   jsonb NOT NULL DEFAULT '[]',
    is_client        boolean NOT NULL DEFAULT false,
    outreach_status  text NOT NULL DEFAULT 'not_contacted'
        CHECK (outreach_status IN ('not_contacted', 'contacted', 'replied', 'client', 'do_not_contact')),
    source           text NOT NULL,
    last_verified_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE company_contact (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    full_name           text NOT NULL,
    title               text,
    email               text,
    linkedin_url        text,
    country             char(2),
    -- CASL needs a recorded consent basis for Canadian recipients (compliance checklist E8, E9).
    consent_basis       text NOT NULL DEFAULT 'none'
        CHECK (consent_basis IN ('none', 'express', 'implied_conspicuous_publication',
                                 'implied_existing_relationship', 'not_required')),
    consent_evidence    text,
    consent_recorded_at timestamptz,
    source              text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX company_contact_company_idx ON company_contact (company_id);

CREATE TABLE app_user (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email             text NOT NULL UNIQUE,
    display_name      text NOT NULL,
    role              text NOT NULL
        CHECK (role IN ('administrator', 'bd_manager', 'recruiter', 'account_manager',
                        'sales_manager', 'client')),
    -- Client users see only their own company's data (Q8).
    client_company_id uuid REFERENCES company (id),
    active            boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK ((role = 'client') = (client_company_id IS NOT NULL))
);

CREATE TABLE job (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_code           text NOT NULL UNIQUE,  -- BRD "Job ID"
    client_company_id  uuid NOT NULL REFERENCES company (id),
    title              text NOT NULL,
    responsibilities   text,
    required_skills    text[] NOT NULL DEFAULT '{}',
    preferred_skills   text[] NOT NULL DEFAULT '{}',
    location           text,
    experience         text,
    visa_requirements  text,
    education          text,
    industry           text,
    travel             text,
    work_mode          text CHECK (work_mode IN ('onsite', 'remote', 'hybrid')),
    salary             text,
    contract_type      text,
    priority           text NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    status             text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'on_hold', 'filled', 'closed')),
    recruiter_id       uuid REFERENCES app_user (id),
    source_email_ref   text,
    parsed_fields      jsonb NOT NULL DEFAULT '{}',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_client_idx ON job (client_company_id);
CREATE INDEX job_status_idx ON job (status);

CREATE TABLE candidate (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name           text NOT NULL,
    email               text,
    phone               text,
    linkedin_url        text,
    current_location    text,
    preferred_location  text,
    skills              text[] NOT NULL DEFAULT '{}',
    projects            jsonb NOT NULL DEFAULT '[]',
    experience_years    numeric(4, 1),
    availability        text,
    notice_period       text,
    visa_status         text,
    work_authorization  text,
    expected_rate       text,
    resume_document_ref text,  -- pointer into the active document store adapter
    resume_parsed       jsonb,
    source              text NOT NULL,
    consent_status      text NOT NULL DEFAULT 'none'
        CHECK (consent_status IN ('none', 'granted', 'withdrawn')),
    consent_recorded_at timestamptz,
    retention_until     date,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Consent to submit a candidate to a specific client (compliance checklist P2).
CREATE TABLE candidate_submission_consent (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id      uuid NOT NULL REFERENCES candidate (id) ON DELETE CASCADE,
    client_company_id uuid NOT NULL REFERENCES company (id),
    job_id            uuid REFERENCES job (id),
    evidence          text NOT NULL,
    granted_at        timestamptz NOT NULL DEFAULT now(),
    revoked_at        timestamptz
);
CREATE INDEX candidate_consent_idx ON candidate_submission_consent (candidate_id, client_company_id);

-- Approval gates (BRD section 10).
CREATE TABLE approval_request (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    gate           text NOT NULL
        CHECK (gate IN ('first_client_email', 'linkedin_connection_request',
                        'candidate_outreach_email', 'resume_submission')),
    subject_type   text NOT NULL,
    subject_id     text NOT NULL,
    requested_by   text NOT NULL,
    content        jsonb NOT NULL,
    content_sha256 char(64) NOT NULL,
    status         text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    resolved_at    timestamptz
);
CREATE INDEX approval_request_pending_idx ON approval_request (gate) WHERE status = 'pending';

CREATE TABLE approval_decision (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_request_id uuid NOT NULL REFERENCES approval_request (id),
    reviewer_id         uuid NOT NULL REFERENCES app_user (id),
    reviewer_role       text NOT NULL,
    action              text NOT NULL CHECK (action IN ('approve', 'edit_and_approve', 'reject')),
    content_sha256      char(64) NOT NULL,
    reason              text,
    decided_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (action <> 'reject' OR reason IS NOT NULL),
    UNIQUE (approval_request_id, reviewer_id)
);

-- Opt-outs apply across every agent and channel (compliance checklist E6, E7).
CREATE TABLE opt_out (
    address     text PRIMARY KEY,  -- normalised email or LinkedIn URL
    source      text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

-- One delivery per approval (Outreach Service, Q7).
CREATE TABLE outreach_message (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_request_id uuid NOT NULL UNIQUE REFERENCES approval_request (id),
    channel             text NOT NULL CHECK (channel IN ('email', 'linkedin')),
    recipient           text NOT NULL,
    delivery_status     text NOT NULL
        CHECK (delivery_status IN ('sent', 'queued_for_manual_send', 'manually_sent',
                                   'bounced', 'failed')),
    provider_message_id text,
    delivered_at        timestamptz NOT NULL DEFAULT now(),
    opened_at           timestamptz,
    replied_at          timestamptz
);

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor       text NOT NULL,
    action      text NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id);
