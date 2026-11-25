import type { Metadata } from "next";
import Link from "next/link";
import { decideAgentRun, startAgentRun } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Status, param } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { AGENT_LABEL, humanize, when } from "@/lib/format";
import type { AgentInfo, AgentRun, Job, Me } from "@/lib/types";

export const metadata: Metadata = { title: "AI agents" };

type Info = { enabled: boolean; has_history?: boolean; agents: AgentInfo[] };

export default async function Agents({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  const info = await api<Info>("/agents");
  const history = info.enabled || Boolean(info.has_history);
  const runs = history ? ((await apiOrNull<AgentRun[]>("/agents/runs")) ?? []) : [];
  const jobs = history ? ((await apiOrNull<Job[]>("/jobs")) ?? []) : [];
  const jobById = new Map(jobs.map((j) => [j.id, j]));
  const runId = param(search, "run");
  const run = runId ? await apiOrNull<AgentRun>(`/agents/runs/${encodeURIComponent(runId)}`) : null;
  const mine = info.agents.filter((a) => a.roles.includes(me.role) && a.name !== "intake");
  const count = (s: AgentRun["status"]) => runs.filter((r) => r.status === s).length;
  const runsOf = (agent: string) => runs.filter((r) => r.agent === agent);

  return (
    <>
      <PageHead
        title="AI agents"
        sub="Deep agents on LangGraph plan the work, delegate to subagents and draft. Every message they prepare still waits in the approval queue."
      />
      <Notice search={search} />
      {!info.enabled && (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          The AI agents are off on this deployment, so the platform runs the same steps without them
          {runs.length > 0 && "; the runs below are earlier or sample runs (test data) and cannot be started or decided here"}.
          To turn them on, set RAP_AGENTS_ENABLED=true with RAP_LLM_PROVIDER=claude (Administrator).
        </div>
      )}

      {runs.length > 0 && (
        <section className="grid cols-4" style={{ marginBottom: 16 }}>
          <Tile label="Runs" value={runs.length} note="visible to your role" />
          <Tile label="Waiting for a person" value={count("waiting_for_human")} note="approve, edit or reject" tone={count("waiting_for_human") ? "warn" : undefined} />
          <Tile label="Completed" value={count("done")} note="report and actions kept" tone="ok" />
          <Tile label="Failed" value={count("failed")} note="nothing was sent" tone={count("failed") ? "bad" : undefined} />
        </section>
      )}

      <section className="grid cols-2" style={{ marginBottom: 16 }}>
        {info.agents.map((a) => {
          const own = runsOf(a.name);
          const last = own[0];
          return (
            <div key={a.name} className="card">
              <div className="card-head">
                <h2>{AGENT_LABEL[a.name] ?? humanize(a.name)}</h2>
                {last ? <Status value={last.status} /> : <span className="faint">No runs</span>}
              </div>
              <div className="card-body stack" style={{ gap: 8 }}>
                <span>{a.goal}</span>
                {a.subagents.length > 0 && <span className="faint">Subagents: {a.subagents.map(humanize).join(", ")}</span>}
                {a.needs_approval.length > 0 && <span className="faint">Pauses for a person before: {a.needs_approval.map(humanize).join(", ")}</span>}
                {a.name === "intake" && <span className="faint">Runs on each email the mailbox reader picks up.</span>}
                {last && (
                  <span className="faint">
                    {own.length} run{own.length === 1 ? "" : "s"} · last <Link href={`/agents?run=${last.id}`}>{when(last.started_at)}</Link> by {last.started_by_name ?? last.started_by}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </section>

      {info.enabled && mine.length > 0 && (
        <section className="card" style={{ marginBottom: 16 }}>
          <div className="card-head"><h2>Start a run</h2></div>
          <div className="card-body stack">
            {mine.map((a) => (
              <form key={a.name} action={startAgentRun} className="row">
                <Back to="/agents" />
                <input type="hidden" name="agent" value={a.name} />
                <strong style={{ minWidth: 190 }}>{AGENT_LABEL[a.name]}</strong>
                {a.name === "recruiting" && (
                  <select name="subject_id" required aria-label="Job">
                    {jobs.map((j) => <option key={j.id} value={j.id}>{j.job_code} · {j.title}</option>)}
                  </select>
                )}
                <input name="instruction" placeholder="Instruction (optional)" style={{ flex: 1, minWidth: 200 }} aria-label="Instruction" />
                <button className="btn primary" type="submit">Start</button>
              </form>
            ))}
          </div>
        </section>
      )}

      {run && <RunDetail run={run} job={run.subject_id ? jobById.get(run.subject_id) : undefined} enabled={info.enabled} />}

      {history && (
        <section className="card">
          <div className="card-head"><h2>Recent runs</h2></div>
          {runs.length === 0 ? <Empty>No runs yet.</Empty> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Agent</th><th>Working on</th><th>Status</th><th>Started</th><th>By</th><th>Steps</th></tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} className={r.id === runId ? "queue-item selected" : undefined}>
                      <td><Link href={`/agents?run=${r.id}`}>{AGENT_LABEL[r.agent] ?? r.agent}</Link></td>
                      <td><Subject run={r} job={r.subject_id ? jobById.get(r.subject_id) : undefined} /></td>
                      <td><Status value={r.status} /></td>
                      <td>{when(r.started_at)}</td>
                      <td>{r.started_by_name ?? r.started_by}</td>
                      <td className="num">{r.actions.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </>
  );
}

function Tile({ label, value, note, tone }: { label: string; value: number; note: string; tone?: "ok" | "warn" | "bad" }) {
  const color = tone ? `var(--${tone})` : undefined;
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value num" style={{ color }}>{value}</div>
      <div className="target">{note}</div>
    </div>
  );
}

function Subject({ run, job }: { run: AgentRun; job?: Job }) {
  if (job) return <Link href={`/jobs/${job.id}`}>{job.job_code} · {job.title}</Link>;
  const what: Record<string, string> = {
    business_development: "Prospects and first emails",
    follow_up: "Due follow-ups",
    intake: "Inbound email",
  };
  return <span className="muted">{what[run.agent] ?? "—"}</span>;
}

/** One tool call as words: "Search and rank · ranked 6 · pool 14". Ids are left out. */
function Step({ action }: { action: Record<string, unknown> }) {
  const details = Object.entries(action)
    .filter(([k, v]) => k !== "tool" && k !== "at" && !k.endsWith("_id") && k !== "job_ids" && v !== null && v !== "")
    .map(([k, v]) => `${humanize(k).toLowerCase()} ${show(v)}`);
  return (
    <li>
      <span className="chip brand" style={{ minWidth: 0 }}>{humanize(String(action.tool ?? ""))}</span>
      <span className="grow">{details.length ? details.join(" · ") : <span className="faint">done</span>}</span>
      <span className="faint">{when(String(action.at ?? ""))}</span>
    </li>
  );
}

function show(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ") || "none";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${humanize(k).toLowerCase()} ${show(v)}`).join(", ");
  }
  return String(value);
}

function RunDetail({ run, job, enabled }: { run: AgentRun; job?: Job; enabled: boolean }) {
  const back = `/agents?run=${run.id}`;
  const waiting = run.pending.flatMap((p) => p.action_requests ?? []);
  return (
    <section className="card" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <h2>{AGENT_LABEL[run.agent] ?? run.agent} run{job ? ` · ${job.job_code}` : ""}</h2>
        <Status value={run.status} />
      </div>
      <div className="card-body stack">
        <dl className="kv">
          <dt>Working on</dt><dd><Subject run={run} job={job} /></dd>
          <dt>Started</dt><dd>{when(run.started_at)} by {run.started_by_name ?? run.started_by}</dd>
          <dt>Last update</dt><dd>{when(run.updated_at)}</dd>
        </dl>
        {run.error && <div className="notice warn">{run.error}</div>}
        {run.summary && (
          <div>
            <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>Report</h3>
            <div className="email-body" style={{ padding: 0 }}>{run.summary}</div>
          </div>
        )}
        {waiting.length > 0 && (
          <div className="notice warn" style={{ flexDirection: "column" }}>
            <strong>Waiting for {enabled ? "your" : "a"} decision</strong>
            {waiting.map((w, i) => (
              <span key={i}>
                <b>{humanize(w.name)}</b>
                {w.description && <>: {w.description}</>}
                <span style={{ display: "block" }} className="faint">{show(w.args)}</span>
              </span>
            ))}
          </div>
        )}
        <div>
          <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>What its tools did</h3>
          {run.actions.length === 0 ? <span className="faint">Nothing yet.</span> : (
            <ol className="list card" style={{ boxShadow: "none" }}>
              {run.actions.map((a, i) => <Step key={i} action={a} />)}
            </ol>
          )}
        </div>
      </div>
      {run.status === "waiting_for_human" && enabled && (
        <div className="card-foot">
          <form action={decideAgentRun}>
            <Back to={back} />
            <input type="hidden" name="run_id" value={run.id} />
            <input type="hidden" name="type" value="approve" />
            <button className="btn ok" type="submit">Approve</button>
          </form>
          <form action={decideAgentRun} className="row">
            <Back to={back} />
            <input type="hidden" name="run_id" value={run.id} />
            <input type="hidden" name="type" value="reject" />
            <input name="message" placeholder="Reason" required aria-label="Reason" />
            <button className="btn danger" type="submit">Reject</button>
          </form>
        </div>
      )}
    </section>
  );
}
