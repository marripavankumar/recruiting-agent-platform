-- 0013: deep-agent runs (LangGraph + deepagents). The conversation state lives in the LangGraph
-- checkpoint tables (thread id = run id); this table is the readable record: who started the
-- run, its status, what its tools did, what it waits for and its final report.

CREATE TABLE agent_run (
    id         uuid PRIMARY KEY,
    agent      text NOT NULL
        CHECK (agent IN ('recruiting', 'business_development', 'intake', 'follow_up')),
    subject_id text,                    -- the job (recruiting) or the email's Message-ID (intake)
    status     text NOT NULL CHECK (status IN ('running', 'waiting_for_human', 'done', 'failed')),
    started_by text NOT NULL,
    started_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    summary    text NOT NULL DEFAULT '',
    actions    jsonb NOT NULL DEFAULT '[]',
    pending    jsonb NOT NULL DEFAULT '[]',
    decisions  jsonb NOT NULL DEFAULT '[]',
    error      text
);
CREATE INDEX agent_run_started_idx ON agent_run (started_at DESC);
CREATE INDEX agent_run_subject_idx ON agent_run (agent, subject_id);
