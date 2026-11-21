-- 0002: approver per gate, editable by the Administrator (Q14). Seeded with the Q14 defaults.

CREATE TABLE gate_policy (
    gate                 text PRIMARY KEY
        CHECK (gate IN ('first_client_email', 'linkedin_connection_request',
                        'candidate_outreach_email', 'resume_submission')),
    first_approver_role  text NOT NULL
        CHECK (first_approver_role IN ('administrator', 'bd_manager', 'recruiter',
                                       'account_manager', 'sales_manager')),
    second_approver_role text
        CHECK (second_approver_role IN ('administrator', 'bd_manager', 'recruiter',
                                        'account_manager', 'sales_manager')),
    updated_by           uuid REFERENCES app_user (id),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

INSERT INTO gate_policy (gate, first_approver_role) VALUES
    ('first_client_email', 'bd_manager'),
    ('linkedin_connection_request', 'recruiter'),
    ('candidate_outreach_email', 'recruiter'),
    ('resume_submission', 'recruiter');
