import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { clientFeedback } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead } from "@/app/components";
import { api } from "@/lib/api";
import { day, humanize, when } from "@/lib/format";
import type { Me } from "@/lib/types";

export const metadata: Metadata = { title: "Your submissions" };

type ClientSubmission = {
  id: string;
  job_code: string | null;
  job_title: string | null;
  candidate_name: string | null;
  sent_at: string;
  subject: string | null;
  package: Record<string, unknown>;
  client_decision: string | null;
  client_feedback: string | null;
  stage: string | null;
  interview_at: string | null;
  start_date: string | null;
};

// Package items shown to the client, in the BRD's order.
const ITEMS: [string, string][] = [
  ["candidate_summary", "Summary"],
  ["match_percent", "Match"],
  ["availability", "Availability"],
  ["rate", "Rate"],
  ["visa", "Work authorisation"],
  ["location", "Location"],
  ["notes", "Recruiter notes"],
];

export default async function Portal({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  if (me.role !== "client") redirect("/");
  const submissions = await api<ClientSubmission[]>("/client/submissions");
  return (
    <>
      <PageHead title="Your submissions" sub="Candidates CBS LLC has put forward for your roles. Tell us who you would like to interview." />
      <Notice search={search} />
      {submissions.length === 0 ? (
        <section className="card"><Empty>No candidates submitted yet. They appear here as soon as your recruiter sends them.</Empty></section>
      ) : (
        <div className="stack">
          {submissions.map((s) => (
            <section key={s.id} className="card">
              <div className="card-head">
                <div>
                  <h2>{s.candidate_name}</h2>
                  <span className="faint">{s.job_title} · {s.job_code} · sent {when(s.sent_at)}</span>
                </div>
                {s.stage && <span className="chip info">{humanize(s.stage)}</span>}
              </div>
              <div className="card-body">
                <dl className="kv">
                  {ITEMS.filter(([k]) => s.package[k] != null && s.package[k] !== "").map(([k, label]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{label}</dt>
                      <dd>{k === "match_percent" ? `${s.package[k]}%` : String(s.package[k])}</dd>
                    </div>
                  ))}
                  {s.interview_at && (<><dt>Interview</dt><dd>{when(s.interview_at)}</dd></>)}
                  {s.start_date && (<><dt>Start date</dt><dd>{day(s.start_date)}</dd></>)}
                </dl>
              </div>
              {s.client_decision ? (
                <div className="card-foot" style={{ justifyContent: "flex-start" }}>
                  <span className="chip ok">Your decision: {humanize(s.client_decision)}</span>
                  {s.client_feedback && <span className="muted">{s.client_feedback}</span>}
                </div>
              ) : (
                <form action={clientFeedback} className="card-foot" style={{ justifyContent: "space-between" }}>
                  <Back to="/portal" />
                  <input type="hidden" name="submission_id" value={s.id} />
                  <label className="grow" style={{ flex: 1 }}>
                    <span className="sr-only">Comment for your recruiter</span>
                    <input name="comment" placeholder="Comment for your recruiter (optional)" style={{ width: "100%" }} />
                  </label>
                  <div className="row">
                    <button className="btn ok" name="decision" value="interview">Interview</button>
                    <button className="btn" name="decision" value="hold">Hold</button>
                    <button className="btn danger" name="decision" value="rejected">Not a fit</button>
                  </div>
                </form>
              )}
            </section>
          ))}
        </div>
      )}
    </>
  );
}
