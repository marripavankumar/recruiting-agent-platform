import type { Metadata } from "next";
import Link from "next/link";
import { Fragment } from "react";
import { decideApproval } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, param } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { GATE_LABEL, ROLE_LABEL, ago, humanize, when } from "@/lib/format";
import type { Approval, Me } from "@/lib/types";

export const metadata: Metadata = { title: "Approval queue" };

const AGENT_NAME: Record<string, string> = {
  "agent-3": "Agent 3 (email outreach)",
  "agent-8": "Agent 8 (candidate outreach)",
  "agent-9": "Agent 9 (resume collection)",
  "agent-11": "Agent 11 (client submission)",
  "agent-12": "Agent 12 (follow-up)",
};

export default async function Approvals({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  const pending = await api<Approval[]>("/approvals/pending");
  const id = param(search, "id") ?? pending[0]?.id;
  const selected = id ? (pending.find((a) => a.id === id) ?? (await apiOrNull<Approval>(`/approvals/${encodeURIComponent(id)}`))) : null;
  const back = selected ? `/approvals?id=${selected.id}` : "/approvals";

  return (
    <>
      <PageHead
        title="Approval queue"
        sub={`Nothing is sent until a person approves it (BRD section 10). Showing what waits for the ${ROLE_LABEL[me.role]}.`}
      />
      <Notice search={search} />
      <div className="split-rev">
        <section className="card" aria-labelledby="queue">
          <div className="card-head"><h2 id="queue">Waiting for you</h2><span className="chip brand">{pending.length}</span></div>
          {pending.length === 0 ? (
            <Empty>Nothing is waiting for your approval.</Empty>
          ) : (
            <ul className="list">
              {pending.map((a) => (
                <li key={a.id} className={a.id === selected?.id ? "queue-item selected" : "queue-item"}>
                  <Link href={`/approvals?id=${a.id}`} className="grow" style={{ color: "inherit" }}>
                    <span className="title">{a.content.subject ?? GATE_LABEL[a.gate]}</span>
                    <span className="sub faint" style={{ display: "block" }}>
                      {GATE_LABEL[a.gate] ?? humanize(a.gate)} · {a.content.to ?? AGENT_NAME[a.requested_by] ?? a.requested_by} · {ago(a.created_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        {selected ? <Detail approval={selected} me={me} back={back} /> : (
          <section className="card"><Empty>Select a request to review it.</Empty></section>
        )}
      </div>
    </>
  );
}

function Detail({ approval, me, back }: { approval: Approval; me: Me; back: string }) {
  const { subject, body, ...other } = approval.content;
  const mayDecide = approval.status === "pending" && approval.next_approver_role === me.role;
  // Only the first approver may edit; a second approver sees the final version.
  const mayEdit = mayDecide && approval.decisions.length === 0;
  const isSubmission = approval.gate === "resume_submission";
  return (
    <section className="card" aria-labelledby="detail">
      <div className="card-head">
        <h2 id="detail">{GATE_LABEL[approval.gate] ?? humanize(approval.gate)}</h2>
        <span className={approval.status === "pending" ? "chip warn" : "chip"}>{humanize(approval.status)}</span>
      </div>
      <div className="card-body stack">
        <dl className="kv">
          <dt>Drafted by</dt><dd>{AGENT_NAME[approval.requested_by] ?? approval.requested_by}</dd>
          <dt>Created</dt><dd>{when(approval.created_at)}</dd>
          <dt>Approver</dt><dd>{approval.next_approver_role ? ROLE_LABEL[approval.next_approver_role] : "—"}</dd>
          {Object.entries(other).map(([k, v]) => (
            <Fragment key={k}><dt>{humanize(k)}</dt><dd>{v}</dd></Fragment>
          ))}
        </dl>

        <div className="email">
          <div className="email-meta"><b>Subject</b><span>{subject ?? "—"}</span></div>
          <div className="email-body">{body ?? ""}</div>
        </div>

        {isSubmission && (
          <div className="row">
            <span className="faint">Client package, exactly as it will be sent:</span>
            <a className="btn sm" href={`/files/submissions/${approval.subject_id}/package?format=pdf`}>PDF</a>
            <a className="btn sm" href={`/files/submissions/${approval.subject_id}/package?format=docx`}>Word</a>
          </div>
        )}

        {approval.decisions.length > 0 && (
          <div>
            <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>Decisions so far</h3>
            <ul className="list">
              {approval.decisions.map((d) => (
                <li key={d.decided_at}>
                  {humanize(d.action)} by {ROLE_LABEL[d.reviewer_role]} · {when(d.decided_at)}
                  {d.reason ? ` · ${d.reason}` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {mayDecide ? (
        <>
          <form action={decideApproval} className="card-foot">
            <Back to={back} />
            <input type="hidden" name="id" value={approval.id} />
            <input type="hidden" name="action" value="approve" />
            <button className="btn ok" type="submit">Approve</button>
          </form>

          {mayEdit && (
            <details className="card-body" style={{ borderTop: "1px solid var(--border)" }}>
              <summary style={{ cursor: "pointer", fontWeight: 600 }}>Edit, then approve</summary>
              <form action={decideApproval} className="stack" style={{ marginTop: 12 }}>
                <Back to={back} />
                <input type="hidden" name="id" value={approval.id} />
                <input type="hidden" name="action" value="edit_and_approve" />
                {Object.entries(approval.content).map(([k, v]) => (
                  <label key={k} className="stack" style={{ gap: 4 }}>
                    <span className="faint">{humanize(k)}</span>
                    {k === "body" ? (
                      <textarea name={`content.${k}`} defaultValue={v} rows={14} />
                    ) : (
                      <input name={`content.${k}`} defaultValue={v} />
                    )}
                  </label>
                ))}
                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <button className="btn ok" type="submit">Save changes and approve</button>
                </div>
              </form>
            </details>
          )}

          <details className="card-body" style={{ borderTop: "1px solid var(--border)" }}>
            <summary style={{ cursor: "pointer", fontWeight: 600, color: "var(--bad)" }}>Reject</summary>
            <form action={decideApproval} className="stack" style={{ marginTop: 12 }}>
              <Back to={back} />
              <input type="hidden" name="id" value={approval.id} />
              <input type="hidden" name="action" value="reject" />
              <label className="stack" style={{ gap: 4 }}>
                <span className="faint">Reason (required, kept in the audit log)</span>
                <textarea name="reason" rows={3} required minLength={3} />
              </label>
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn danger" type="submit">Reject</button>
              </div>
            </form>
          </details>
        </>
      ) : (
        <div className="card-foot faint">
          {approval.status === "pending" ? "Waiting for another approver." : "This request is already decided."}
        </div>
      )}
    </section>
  );
}
