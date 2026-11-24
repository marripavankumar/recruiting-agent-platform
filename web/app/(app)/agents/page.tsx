import type { Metadata } from "next";
import Link from "next/link";
import { decideAgentRun, startAgentRun } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Status, param } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { AGENT_LABEL, humanize, when } from "@/lib/format";
import type { AgentInfo, AgentRun, Job, Me } from "@/lib/types";

export const metadata: Metadata = { title: "AI agents" };

export default async function Agents({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  const info = await api<{ enabled: boolean; agents: AgentInfo[] }>("/agents");
  const runs = info.enabled ? ((await apiOrNull<AgentRun[]>("/agents/runs")) ?? []) : [];
  const jobs = info.enabled ? ((await apiOrNull<Job[]>("/jobs")) ?? []) : [];
  const runId = param(search, "run");
  const run = runId ? await apiOrNull<AgentRun>(`/agents/runs/${encodeURIComponent(runId)}`) : null;
  const mine = info.agents.filter((a) => a.roles.includes(me.role) && a.name !== "intake");

  return (
    <>
      <PageHead
        title="AI agents"
        sub="Deep agents on LangGraph plan the work, delegate to subagents and draft. Every message they prepare still waits in the approval queue."
      />
      <Notice search={search} />
      {!info.enabled && (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          The AI agents are off on this deployment. The platform runs the same steps without them. To turn them on, set
          RAP_AGENTS_ENABLED=true with RAP_LLM_PROVIDER=claude (Administrator).
        </div>
      )}

      <section className="grid cols-2" style={{ marginBottom: 16 }}>
        {info.agents.map((a) => (
          <div key={a.name} className="card">
            <div className="card-head"><h2>{AGENT_LABEL[a.name] ?? humanize(a.name)}</h2></div>
            <div className="card-body stack" style={{ gap: 8 }}>
              <span>{a.goal}</span>
              {a.subagents.length > 0 && <span className="faint">Subagents: {a.subagents.map(humanize).join(", ")}</span>}
              {a.needs_approval.length > 0 && <span className="faint">Pauses for a person before: {a.needs_approval.map(humanize).join(", ")}</span>}
              {a.name === "intake" && <span className="faint">Runs on each email the mailbox reader picks up.</span>}
            </div>
          </div>
        ))}
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

      {run && <RunDetail run={run} />}

      {info.enabled && (
        <section className="card">
          <div className="card-head"><h2>Recent runs</h2></div>
          {runs.length === 0 ? <Empty>No runs yet.</Empty> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Agent</th><th>Status</th><th>Started</th><th>By</th><th>Actions</th></tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id}>
                      <td><Link href={`/agents?run=${r.id}`}>{AGENT_LABEL[r.agent] ?? r.agent}</Link></td>
                      <td><Status value={r.status} /></td>
                      <td>{when(r.started_at)}</td>
                      <td>{r.started_by}</td>
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

function RunDetail({ run }: { run: AgentRun }) {
  const back = `/agents?run=${run.id}`;
  const waiting = run.pending.flatMap((p) => p.action_requests ?? []);
  return (
    <section className="card" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <h2>{AGENT_LABEL[run.agent] ?? run.agent} run</h2>
        <Status value={run.status} />
      </div>
      <div className="card-body stack">
        {run.error && <div className="notice warn">{run.error}</div>}
        {run.summary && <div className="email-body" style={{ padding: 0 }}>{run.summary}</div>}
        {waiting.length > 0 && (
          <div className="notice warn" style={{ flexDirection: "column" }}>
            <strong>Waiting for your decision</strong>
            {waiting.map((w, i) => (
              <code key={i}>{humanize(w.name)}: {JSON.stringify(w.args)}</code>
            ))}
          </div>
        )}
        <div>
          <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>What its tools did</h3>
          {run.actions.length === 0 ? <span className="faint">Nothing yet.</span> : (
            <ul className="list">
              {run.actions.map((a, i) => (
                <li key={i}><code>{String(a.tool ?? "")}</code><span className="faint grow">{JSON.stringify({ ...a, tool: undefined })}</span></li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {run.status === "waiting_for_human" && (
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
