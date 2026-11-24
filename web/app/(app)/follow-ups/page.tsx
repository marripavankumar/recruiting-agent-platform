import type { Metadata } from "next";
import Link from "next/link";
import { followUpAction, runFollowUpSweep } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead } from "@/app/components";
import { api } from "@/lib/api";
import { day, humanize } from "@/lib/format";
import type { FollowUp } from "@/lib/types";

export const metadata: Metadata = { title: "Follow-ups" };

// Follow-ups that are a message to a client or candidate: they can be drafted for approval.
const MESSAGE_KINDS = new Set([
  "client_outreach", "submission_feedback", "candidate_outreach", "candidate_details", "interview_feedback",
]);

export default async function FollowUps({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const items = await api<FollowUp[]>("/follow-ups");
  const now = Date.now();
  const overdue = items.filter((i) => i.due_at && new Date(i.due_at).getTime() < now);
  return (
    <>
      <PageHead title="Follow-ups" sub="Every open client, candidate and placement step has a next date (Agent 12). Yours, soonest first.">
        <form action={runFollowUpSweep}>
          <Back to="/follow-ups" />
          <button className="btn" type="submit">Bring up to date</button>
        </form>
      </PageHead>
      <Notice search={search} />
      <section className="card">
        <div className="card-head">
          <h2>Open</h2>
          <span className="row">
            <span className="chip">{items.length}</span>
            {overdue.length > 0 && <span className="chip bad">{overdue.length} overdue</span>}
          </span>
        </div>
        {items.length === 0 ? <Empty>Nothing to follow up. The sweep runs every hour.</Empty> : (
          <ul className="list">
            {items.map((item) => {
              const late = item.due_at !== null && new Date(item.due_at).getTime() < now;
              return (
                <li key={item.id}>
                  <div className="grow">
                    <div className="title">{humanize(item.kind)}</div>
                    <div>{item.note}</div>
                    <div className="faint">
                      Due {day(item.due_at)}{late ? " (overdue)" : ""}
                      {item.attempt > 0 ? ` · follow-up ${item.attempt} sent` : ""}
                      {item.job_id ? <> · <Link href={`/jobs/${item.job_id}?tab=candidates`}>open the job</Link></> : null}
                    </div>
                  </div>
                  <div className="row">
                    {MESSAGE_KINDS.has(item.kind) && item.approvals.length === 0 && (
                      <Action id={item.id} action="draft" label="Draft follow-up" primary />
                    )}
                    {item.approvals.length > 0 && <Link className="btn sm" href="/approvals">In approval queue</Link>}
                    <form action={followUpAction} className="row">
                      <Back to="/follow-ups" />
                      <input type="hidden" name="id" value={item.id} />
                      <input type="hidden" name="action" value="snooze" />
                      <label className="sr-only" htmlFor={`days-${item.id}`}>Snooze for</label>
                      <select id={`days-${item.id}`} name="days" defaultValue="1">
                        <option value="1">1 day</option>
                        <option value="3">3 days</option>
                        <option value="7">1 week</option>
                      </select>
                      <button className="btn sm" type="submit">Snooze</button>
                    </form>
                    <Action id={item.id} action="complete" label="Done" />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}

function Action({ id, action, label, primary }: { id: string; action: string; label: string; primary?: boolean }) {
  return (
    <form action={followUpAction}>
      <Back to="/follow-ups" />
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="action" value={action} />
      <button className={primary ? "btn sm primary" : "btn sm"} type="submit">{label}</button>
    </form>
  );
}
