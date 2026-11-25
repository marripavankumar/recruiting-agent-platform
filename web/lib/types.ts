// Shapes returned by the FastAPI backend (only the fields the screens use).

export type Role =
  | "administrator"
  | "bd_manager"
  | "recruiter"
  | "account_manager"
  | "sales_manager"
  | "client";

export type Me = {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  client_company_id: string | null;
  environment: string;
};

export type Decision = {
  reviewer_id: string;
  reviewer_role: Role;
  action: string;
  reason: string | null;
  decided_at: string;
};

export type Approval = {
  id: string;
  gate: string;
  subject_id: string;
  requested_by: string;
  content: Record<string, string>;
  status: string;
  next_approver_role: Role | null;
  created_at: string;
  resolved_at: string | null;
  decisions: Decision[];
};

export type Kpi = {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  target: string;
  status: "on_track" | "below_target" | "no_data" | string;
  basis: Record<string, number>;
};

export type KpiReport = {
  from: string;
  to: string;
  days: number;
  kpis: Kpi[];
  funnel: Record<string, number>;
};

export type Operations = {
  email_30d: { sent: number; failed: number; bounced: number; failure_rate: number; bounce_rate: number };
  approvals_pending: { total: number; by_gate: Record<string, number>; oldest_hours: number };
  llm_30d: { calls: number; cost_usd: number };
  follow_ups: { open: number; overdue: number };
  alerts: string[];
};

export type JobAnalysis = {
  must_have_skills: string[];
  nice_to_have_skills: string[];
  years_experience_min: number | null;
  search_summary: string;
  missing_info: string[];
};

export type Job = {
  id: string;
  job_code: string;
  client_company_id: string;
  title: string;
  status: string;
  priority: string;
  recruiter_id: string | null;
  required_skills: string[];
  preferred_skills: string[];
  review_fields: string[];
  analysis: JobAnalysis | null;
  created_at: string;
} & Record<string, unknown>;

export type RankedCandidate = {
  candidate_id: string;
  score: number;
  strengths: string[];
  gaps: string[];
  rank: number | null;
  excluded_reason: string | null;
  verdict: string | null;
  note: string | null;
  full_name: string;
  location: string | null;
  source: string;
};

export type Ranking = {
  job_id: string;
  ranked_at: string;
  searched: number;
  qualified_in_top_20: number;
  shortfall: {
    qualified: number;
    target: number;
    widened: boolean;
    suggestions: { title_variants: string[]; location: string[]; include_nice_to_have: boolean };
  } | null;
  candidates: RankedCandidate[];
};

export type Engagement = {
  id: string;
  candidate_id: string;
  candidate_name: string;
  status: string;
  missing: string[];
  consent_recorded: boolean;
  updated_at: string | null;
};

export type Submission = {
  id: string;
  job_id: string;
  candidate_id: string;
  status: string;
  stage?: string | null;
  match: {
    score: number;
    strengths: string[];
    concerns: string[];
    missing_skills: string[];
    flags: string[];
    interview_probability: number | null;
    summary: string;
  } | null;
  package: Record<string, unknown> & { candidate_name?: string; job_title?: string };
  recipient: string | null;
  approval_request_id: string | null;
  warnings: string[];
  rejection_reason: string | null;
  sent_at: string | null;
  client_decision: string | null;
  client_feedback: string | null;
};

export type FollowUp = {
  id: string;
  kind: string;
  owner_role: Role;
  due_at: string | null;
  status: string;
  attempt: number;
  job_id: string | null;
  candidate_id: string | null;
  company_id: string | null;
  note: string;
  approvals: string[];
};

export type AgentInfo = {
  name: string;
  goal: string;
  roles: Role[];
  subagents: string[];
  needs_approval: string[];
};

export type AgentRun = {
  id: string;
  agent: string;
  subject_id: string | null;
  status: "running" | "waiting_for_human" | "done" | "failed";
  started_by: string;
  started_by_name?: string;
  started_at: string;
  updated_at: string;
  summary: string;
  actions: Record<string, unknown>[];
  pending: { interrupt_id?: string; action_requests?: { name: string; args: Record<string, unknown>; description?: string }[] }[];
  error: string | null;
};
