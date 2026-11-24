"use server";

// Server actions behind every form. Each one calls the API as the signed-in user, then returns
// to the page it came from (the hidden "back" field) with ?ok= or ?error= for the message bar.
// The API enforces roles and approval rules; the web app never decides them itself.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL, ApiError, api } from "@/lib/api";
import { WEIGHT_KEYS } from "@/lib/format";
import { discover, oidcConfig } from "@/lib/oidc";
import { SESSION_COOKIE, TOKEN_COOKIE, authMode, secureCookies } from "@/lib/session";

type Body = Record<string, unknown>;

function text(form: FormData, key: string): string | undefined {
  const value = form.get(key);
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function backTo(form: FormData, params: Record<string, string>): string {
  const back = text(form, "back") ?? "/";
  // Only same-site paths: never redirect to a URL taken from the form.
  const url = new URL(back.startsWith("/") ? back : "/", "http://local");
  url.searchParams.delete("ok");
  url.searchParams.delete("error");
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  return url.pathname + url.search;
}

/** Run one API call and go back with its outcome. */
async function act(form: FormData, success: string, call: () => Promise<unknown>): Promise<never> {
  let target: string;
  try {
    await call();
    target = backTo(form, { ok: success });
  } catch (error) {
    if (!(error instanceof ApiError)) throw error; // includes Next's redirect to sign-in
    target = backTo(form, { error: error.message });
  }
  redirect(target);
}

// ----- sign-in ------------------------------------------------------------------------------

export async function signIn(form: FormData): Promise<void> {
  if (authMode() !== "dev") redirect("/signin?error=" + encodeURIComponent("Demo sign-in is off"));
  const email = text(form, "email")?.toLowerCase();
  if (!email) redirect("/signin?error=" + encodeURIComponent("Choose a user"));
  let problem: string | null = null;
  try {
    const response = await fetch(`${API_URL}/me`, {
      headers: { "X-Dev-User-Email": email },
      cache: "no-store",
    });
    if (response.status === 403) problem = `${email} is not a platform user`;
    else if (!response.ok) problem = `Sign-in failed (${response.status})`;
  } catch {
    problem = `The platform API is not reachable at ${API_URL}`;
  }
  if (problem) redirect("/signin?error=" + encodeURIComponent(problem));
  (await cookies()).set(SESSION_COOKIE, email, {
    httpOnly: true,
    sameSite: "lax",
    secure: secureCookies(),
    path: "/",
    maxAge: 60 * 60 * 12,
  });
  redirect("/");
}

export async function signOut(): Promise<void> {
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  jar.delete(SESSION_COOKIE);
  jar.delete(TOKEN_COOKIE);
  const config = oidcConfig();
  let target = "/signin";
  if (authMode() === "oidc" && config && token) {
    // Also end the provider session where it supports RP-initiated logout.
    const end = await discover(config).then((d) => d.end_session_endpoint).catch(() => undefined);
    if (end) {
      const url = new URL(end);
      url.searchParams.set("id_token_hint", token);
      url.searchParams.set("post_logout_redirect_uri", config.postLogoutUri);
      target = url.toString();
    }
  }
  redirect(target);
}

// ----- approvals (BRD section 10) -----------------------------------------------------------

export async function decideApproval(form: FormData): Promise<void> {
  const id = text(form, "id")!;
  const action = text(form, "action");
  const body: Body = { action };
  if (action === "reject") body.reason = text(form, "reason");
  if (action === "edit_and_approve") {
    const edited: Record<string, string> = {};
    for (const [key, value] of form.entries()) {
      if (key.startsWith("content.") && typeof value === "string") edited[key.slice(8)] = value;
    }
    body.edited_content = edited;
  }
  const done = action === "reject" ? "Rejected" : "Approved";
  await act(form, done, () =>
    api(`/approvals/${encodeURIComponent(id)}/decision`, { method: "POST", body }),
  );
}

// ----- jobs (Agents 4-7) --------------------------------------------------------------------

const JOB_FIELDS = [
  "title", "location", "experience", "visa_requirements", "education", "industry", "travel",
  "work_mode", "salary", "contract_type", "priority", "responsibilities",
];

export async function updateJob(form: FormData): Promise<void> {
  const id = text(form, "id")!;
  const body: Body = {};
  for (const field of JOB_FIELDS) {
    const value = text(form, field);
    if (value !== undefined && value !== text(form, `was.${field}`)) body[field] = value;
  }
  body.confirm_fields = form.getAll("confirm").filter((v): v is string => typeof v === "string");
  await act(form, "Job updated", () => api(`/jobs/${encodeURIComponent(id)}`, { method: "PATCH", body }));
}

export async function rankJob(form: FormData): Promise<void> {
  const id = text(form, "job_id")!;
  await act(form, "Search and ranking done", () =>
    api(`/jobs/${encodeURIComponent(id)}/ranking`, { method: "POST" }),
  );
}

export async function reviewCandidate(form: FormData): Promise<void> {
  const job = text(form, "job_id")!;
  const candidate = text(form, "candidate_id")!;
  await act(form, "Verdict saved", () =>
    api(`/jobs/${encodeURIComponent(job)}/ranking/${encodeURIComponent(candidate)}/review`, {
      method: "POST",
      body: { verdict: text(form, "verdict"), note: text(form, "note") },
    }),
  );
}

export async function draftCandidateOutreach(form: FormData): Promise<void> {
  const job = text(form, "job_id")!;
  const ids = form.getAll("candidate_id").filter((v): v is string => typeof v === "string");
  await act(form, "Outreach drafted: waiting in the approval queue", () =>
    api(`/jobs/${encodeURIComponent(job)}/outreach`, {
      method: "POST",
      body: { candidate_ids: ids, channels: form.getAll("channel") },
    }),
  );
}

export async function requestDetails(form: FormData): Promise<void> {
  const id = text(form, "engagement_id")!;
  await act(form, "Details request drafted: waiting in the approval queue", () =>
    api(`/engagements/${encodeURIComponent(id)}/request-details`, { method: "POST" }),
  );
}

// ----- submissions (Agents 10-11) -----------------------------------------------------------

export async function prepareSubmission(form: FormData): Promise<void> {
  const job = text(form, "job_id")!;
  await act(form, "Match report and package prepared", () =>
    api(`/jobs/${encodeURIComponent(job)}/submissions`, {
      method: "POST",
      body: { candidate_id: text(form, "candidate_id") },
    }),
  );
}

export async function requestSubmissionApproval(form: FormData): Promise<void> {
  const id = text(form, "submission_id")!;
  await act(form, "Submission waiting for approval", () =>
    api(`/submissions/${encodeURIComponent(id)}/request-approval`, {
      method: "POST",
      body: {
        right_to_represent: form.get("right_to_represent") === "on",
        acknowledge_flags: form.get("acknowledge_flags") === "on",
        recipient: text(form, "recipient"),
      },
    }),
  );
}

export async function clientFeedback(form: FormData): Promise<void> {
  const id = text(form, "submission_id")!;
  await act(form, "Thank you: your feedback was sent to CBS LLC", () =>
    api(`/client/submissions/${encodeURIComponent(id)}/feedback`, {
      method: "POST",
      body: { decision: text(form, "decision"), comment: text(form, "comment") },
    }),
  );
}

// ----- follow-ups (Agent 12) ----------------------------------------------------------------

export async function followUpAction(form: FormData): Promise<void> {
  const id = encodeURIComponent(text(form, "id")!);
  const action = text(form, "action");
  if (action === "complete") {
    await act(form, "Marked done", () =>
      api(`/follow-ups/${id}/complete`, { method: "POST", body: { note: text(form, "note") } }),
    );
  }
  if (action === "snooze") {
    const days = Number(text(form, "days") ?? "1");
    const until = new Date(Date.now() + days * 86_400_000).toISOString();
    await act(form, `Snoozed for ${days} day${days === 1 ? "" : "s"}`, () =>
      api(`/follow-ups/${id}/snooze`, { method: "POST", body: { until } }),
    );
  }
  await act(form, "Follow-up drafted: waiting in the approval queue", () =>
    api(`/follow-ups/${id}/draft`, { method: "POST" }),
  );
}

export async function runFollowUpSweep(form: FormData): Promise<void> {
  await act(form, "Follow-ups brought up to date", () => api("/follow-ups/run", { method: "POST" }));
}

// ----- deep agents --------------------------------------------------------------------------

export async function startAgentRun(form: FormData): Promise<void> {
  const agent = text(form, "agent")!;
  await act(form, "Agent run started", () =>
    api(`/agents/${encodeURIComponent(agent)}/runs`, {
      method: "POST",
      body: { subject_id: text(form, "subject_id"), instruction: text(form, "instruction") },
    }),
  );
}

export async function decideAgentRun(form: FormData): Promise<void> {
  const id = text(form, "run_id")!;
  const type = text(form, "type");
  await act(form, type === "reject" ? "Rejected: the agent carries on without it" : "Approved: the agent carries on", () =>
    api(`/agents/runs/${encodeURIComponent(id)}/decisions`, {
      method: "POST",
      body: { decisions: [{ type, message: text(form, "message") }] },
    }),
  );
}

// ----- companies and client outreach (Agents 1-3) -------------------------------------------

/** Like act(), but the success message is built from the API's answer. */
async function actWith<T>(form: FormData, call: () => Promise<T>, message: (result: T) => string): Promise<never> {
  let target: string;
  try {
    target = backTo(form, { ok: message(await call()) });
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    target = backTo(form, { error: error.message });
  }
  redirect(target);
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const reasons = (counts: Record<string, number>) =>
  Object.entries(counts).map(([why, n]) => `${n} ${why.replace(/_/g, " ")}`).join(", ");
const ids = (form: FormData, key: string) => form.getAll(key).filter((v): v is string => typeof v === "string" && v !== "");

export async function importCompanies(form: FormData): Promise<void> {
  const file = form.get("file");
  let csv = text(form, "csv") ?? "";
  if (file instanceof File && file.size > 0) csv = await file.text();
  if (!csv.trim()) redirect(backTo(form, { error: "Choose a CSV file or paste the rows" }));
  type Report = { seen: number; created: number; updated: number; merged: number; contacts_added: number; skipped: Record<string, number> };
  await actWith(
    form,
    () => api<Report>("/companies/import", { method: "POST", body: { source_name: text(form, "source_name") ?? "upload", csv } }),
    (r) =>
      `${plural(r.seen, "row")}: ${plural(r.created, "new company")}, ${r.updated} updated, ${r.merged} merged, ` +
      `${plural(r.contacts_added, "contact")} added` + (Object.keys(r.skipped).length ? `; skipped ${reasons(r.skipped)}` : ""),
  );
}

export async function checkHiringSignals(form: FormData): Promise<void> {
  const chosen = ids(form, "company_id");
  type Report = { companies_checked: number; pages_unavailable: number; target_postings: number; new_postings: number; expired: number };
  await actWith(
    form,
    () => api<Report>("/companies/hiring-signals", { method: "POST", body: { company_ids: chosen.length ? chosen : null } }),
    (r) =>
      `${plural(r.companies_checked, "career page")} read: ${r.target_postings} target roles open, ${r.new_postings} new, ${r.expired} expired` +
      (r.pages_unavailable ? `, ${r.pages_unavailable} unavailable` : ""),
  );
}

export async function draftClientEmails(form: FormData): Promise<void> {
  const chosen = ids(form, "company_id");
  type Report = { drafted: unknown[]; skipped: Record<string, string[]> };
  await actWith(
    form,
    () => api<Report>("/companies/outreach-drafts", { method: "POST", body: { company_ids: chosen.length ? chosen : null } }),
    (r) => {
      const skipped = Object.fromEntries(Object.entries(r.skipped).map(([k, v]) => [k, v.length]));
      return `${plural(r.drafted.length, "first email")} drafted for approval` +
        (Object.keys(skipped).length ? `; skipped ${reasons(skipped)}` : "");
    },
  );
}

// ----- LinkedIn (Q15) and engagement outcomes -----------------------------------------------

export async function markLinkedInSent(form: FormData): Promise<void> {
  const id = text(form, "approval_request_id")!;
  await act(form, "Marked as sent in LinkedIn", () =>
    api(`/linkedin-tasks/${encodeURIComponent(id)}/sent`, { method: "POST" }),
  );
}

export async function recordOutcome(form: FormData): Promise<void> {
  const id = text(form, "engagement_id")!;
  const outcome = text(form, "outcome") ?? "";
  await act(form, `Recorded: ${outcome.replace(/_/g, " ")}`, () =>
    api(`/engagements/${encodeURIComponent(id)}/outcome`, { method: "POST", body: { outcome } }),
  );
}

// ----- candidates (Agents 6-7) --------------------------------------------------------------

export async function uploadResumes(form: FormData): Promise<void> {
  const upload = new FormData();
  for (const file of form.getAll("files")) if (file instanceof File && file.size > 0) upload.append("files", file, file.name);
  if (!upload.has("files")) redirect(backTo(form, { error: "Choose at least one resume file" }));
  upload.append("source", text(form, "source") ?? "manual");
  upload.append("consent", text(form, "consent") ?? "none");
  type Report = { created: number; updated: number; errors: { filename: string; error: string }[] };
  let target: string;
  try {
    const r = await api<Report>("/candidates/upload", { method: "POST", form: upload });
    const ok = `${plural(r.created, "new candidate")}, ${r.updated} updated`;
    const failed = r.errors.map((e) => (e.error.startsWith(e.filename) ? e.error : `${e.filename}: ${e.error}`)).join("; ");
    target = backTo(form, failed ? { ok, error: failed } : { ok });
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    target = backTo(form, { error: error.message });
  }
  redirect(target);
}

export async function addResumeText(form: FormData): Promise<void> {
  const resume = text(form, "resume_text");
  if (!resume) redirect(backTo(form, { error: "Paste the resume text" }));
  type Report = { created: number; updated: number };
  await actWith(
    form,
    () => api<Report>("/candidates", { method: "POST", body: { resumes: [{ resume_text: resume, source: text(form, "source") ?? "manual" }] } }),
    (r) => (r.created ? "Candidate added" : "Existing candidate updated (same email, phone or LinkedIn)"),
  );
}

export async function widenSearch(form: FormData): Promise<void> {
  const job = text(form, "job_id")!;
  await act(form, "Widened search done: the ranking is updated", () =>
    api(`/jobs/${encodeURIComponent(job)}/ranking/widen`, {
      method: "POST",
      body: {
        title_variants: ids(form, "title_variant"),
        location: text(form, "location") ?? null,
        include_nice_to_have: form.get("include_nice_to_have") === "on",
      },
    }),
  );
}

export async function eraseCandidate(form: FormData): Promise<void> {
  const id = text(form, "candidate_id")!;
  await act(form, "Personal data erased; the candidate will not be contacted again", () =>
    api(`/candidates/${encodeURIComponent(id)}/erase`, { method: "POST", body: { reason: text(form, "reason") } }),
  );
}

// ----- administration -----------------------------------------------------------------------

export async function createUser(form: FormData): Promise<void> {
  await act(form, "User added", () =>
    api("/admin/users", {
      method: "POST",
      body: {
        email: text(form, "email"),
        display_name: text(form, "display_name"),
        role: text(form, "role"),
        client_company_id: text(form, "client_company_id") ?? null,
      },
    }),
  );
}

export async function updateUser(form: FormData): Promise<void> {
  const id = text(form, "user_id")!;
  const body: Body = {};
  if (text(form, "role")) body.role = text(form, "role");
  if (text(form, "active")) body.active = text(form, "active") === "true";
  await act(form, "User updated", () => api(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body }));
}

export async function setGatePolicy(form: FormData): Promise<void> {
  const gate = text(form, "gate")!;
  await act(form, "Approval policy saved: it applies to new requests now", () =>
    api(`/admin/gate-policies/${encodeURIComponent(gate)}`, {
      method: "PUT",
      body: { first_approver_role: text(form, "first_approver_role"), second_approver_role: text(form, "second_approver_role") ?? null },
    }),
  );
}

export async function setRankingWeights(form: FormData): Promise<void> {
  const body: Body = {};
  for (const key of [...WEIGHT_KEYS, "min_must_have_coverage"]) body[key] = Number(text(form, key) ?? "0");
  await act(form, "Ranking weights saved: they apply to the next ranking", () =>
    api("/admin/ranking-weights", { method: "PUT", body }),
  );
}

export async function setCadence(form: FormData): Promise<void> {
  const kind = text(form, "kind")!;
  const days = (text(form, "days") ?? "").split(/[\s,]+/).filter(Boolean).map(Number);
  if (days.length === 0 || days.some((d) => !Number.isInteger(d))) {
    redirect(backTo(form, { error: "Days must be whole numbers, for example 3, 7, 14" }));
  }
  await act(form, "Cadence saved", () =>
    api(`/admin/follow-up-cadence/${encodeURIComponent(kind)}`, { method: "PUT", body: { days } }),
  );
}
