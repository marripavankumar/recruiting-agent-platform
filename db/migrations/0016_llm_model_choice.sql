-- 0016: the AI model the Administrator chose (RAP_LLM_PROVIDER=openrouter). One row; the LLM
-- gateway and the deep agents read it on every call, so a change applies without a restart.
-- Each change is also written to audit_log.

CREATE TABLE llm_settings (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    model      text NOT NULL,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
