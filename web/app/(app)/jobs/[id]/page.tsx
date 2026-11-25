import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import {
  draftCandidateOutreach,
  prepareSubmission,
  rankJob,
  recordOutcome,
  requestDetails,
  requestSubmissionApproval,
  reviewCandidate,
  updateJob,
  widenSearch,
} from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Score, Status, Tabs, param } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { day, humanize, when } from "@/lib/format";
import type { Engagement, Job, Me, Ranking, Submission } from "@/lib/types";

export const metadata: Metadata = { title: "Job" };

const TABS: [string, string][] = [
  ["details", "Job details"],
  ["candidates", "Ranked candidates"],
  ["engagement", "Engagement"],
  ["submissions", "Submissions"],
];

// Fields the Account Manager reviews (Agent 4 marks the uncertain ones).
const FIELDS: [string, string][] = [
  ["title", "Title"],
  ["location", "Location"],
  ["experience", "Experience"],
  ["visa_requirements", "Visa / work authorisation"],
  ["education", "Education"],
  ["industry", "Industry"],
  ["work_mode", "Work mode"],
  ["travel", "Travel"],
  ["salary", "Rate / salary"],
  ["contract_type", "Contract type"],
  ["priority", "Priority"],
  ["responsibilities", "Responsibilities"],
];

const CHOICES: Record<string, string[]> = {
  work_mode: ["onsite", "hybrid", "remote"],
  priority: ["low", "medium", "high", "urgent"],
};

type Engagements = { summary: Record<string, number | null>; engagements: Engagement[] };
type Submissions = { summary: Record<string, number | null>; submissions: Submission[] };

export default async function JobPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = await params;
  const search = await searchParams;
  const tab = TABS.some(([k]) => k === param(search, "tab")) ? param(search, "tab")! : "details";
  // Staff page: clients follow their jobs and submissions in the portal.
  if ((await api<Me>("/me")).role === "client") redirect("/portal");
  const job = await api<Job>(`/jobs/${encodeURIComponent(id)}`);
  const base = `/jobs/${job.id}`;
  const back = `${base}?tab=${tab}`;
  return (
    <>
      <PageHead title={job.title} crumbs={<><Link href="/jobs">Jobs</Link> / {job.job_code}</>} sub={job.analysis?.search_summary}>
        <Status value={job.status} />
        {job.review_fields.length > 0 && <span className="chip warn">{job.review_fields.length} fields to confirm</span>}
      </PageHead>
      <Notice search={search} />
      <Tabs base={base} current={tab} tabs={TABS} />
      {tab === "details" && <Details job={job} back={back} />}
      {tab === "candidates" && <Candidates job={job} back={back} />}
      {tab === "engagement" && <EngagementTab job={job} back={back} />}
      {tab === "submissions" && <SubmissionsTab job={job} back={back} />}
    </>
  );
}

function Details({ job, back }: { job: Job; back: string }) {
  const review = new Set(job.review_fields);
  const a = job.analysis;
  return (
    <div className="split">
      <form action={updateJob} className="card">
        <Back to={back} />
        <input type="hidden" name="id" value={job.id} />
        <div className="card-head">
          <h2>Extracted fields</h2>
          <span className="faint">Highlighted fields were uncertain: correct them or tick to confirm.</span>
        </div>
        <div className="card-body stack" style={{ gap: 12 }}>
          {FIELDS.map(([key, label]) => {
            const value = (job[key] as string | null | undefined) ?? "";
            const uncertain = review.has(key);
            return (
              <div key={key} className={uncertain ? "field-review" : undefined} style={{ padding: uncertain ? 8 : 0 }}>
                <label className="stack" style={{ gap: 4 }}>
                  <span className="faint">{label}{uncertain ? " · to confirm" : ""}</span>
                  <input type="hidden" name={`was.${key}`} value={value} />
                  {CHOICES[key] ? (
                    <select name={key} defaultValue={value}>
                      <option value="">Not stated</option>
                      {CHOICES[key].map((c) => <option key={c} value={c}>{humanize(c)}</option>)}
                    </select>
                  ) : key === "responsibilities" ? (
                    <textarea name={key} defaultValue={value} rows={4} />
                  ) : (
                    <input name={key} defaultValue={value} placeholder="Not stated" />
                  )}
                </label>
                {uncertain && (
                  <label className="row faint" style={{ marginTop: 6 }}>
                    <input type="checkbox" name="confirm" value={key} /> Checked: correct as shown
                  </label>
                )}
              </div>
            );
          })}
        </div>
        <div className="card-foot">
          <button className="btn primary" type="submit">Save and confirm</button>
        </div>
      </form>

      <div className="stack">
        <section className="card">
          <div className="card-head"><h2>Search profile</h2><span className="faint">Agent 5</span></div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <div>
              <div className="faint">Must have</div>
              {(a?.must_have_skills ?? job.required_skills).map((s) => <span key={s} className="tag have">{s}</span>)}
            </div>
            {(a?.nice_to_have_skills.length ?? 0) > 0 && (
              <div>
                <div className="faint">Nice to have</div>
                {a!.nice_to_have_skills.map((s) => <span key={s} className="tag">{s}</span>)}
              </div>
            )}
            {a?.years_experience_min != null && <div>{a.years_experience_min}+ years of experience</div>}
            {(a?.missing_info.length ?? 0) > 0 && (
              <div className="notice warn">Not in the JD: {a!.missing_info.join(", ")}. Ask the client.</div>
            )}
          </div>
        </section>
        <section className="card">
          <div className="card-body">
            <dl className="kv">
              <dt>Job code</dt><dd>{job.job_code}</dd>
              <dt>Received</dt><dd>{when(job.created_at)}</dd>
              <dt>Source email</dt><dd className="mono">{(job.source_email_ref as string) ?? "—"}</dd>
            </dl>
          </div>
        </section>
      </div>
    </div>
  );
}

async function Candidates({ job, back }: { job: Job; back: string }) {
  const ranking = await apiOrNull<Ranking>(`/jobs/${job.id}/ranking`);
  const ranked = ranking?.candidates.filter((c) => c.rank !== null) ?? [];
  const excluded = ranking?.candidates.filter((c) => c.rank === null) ?? [];
  const pendingReview = job.review_fields.some((f) => ["title", "location", "visa_requirements"].includes(f));
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="muted">
          {ranking
            ? `${ranking.searched} candidates searched · ${ranking.qualified_in_top_20} marked qualified in the top 20 · ranked ${when(ranking.ranked_at)}`
            : "Not searched yet."}
        </span>
        <form action={rankJob}>
          <Back to={back} />
          <input type="hidden" name="job_id" value={job.id} />
          <button className="btn primary" type="submit">{ranking ? "Search and rank again" : "Search and rank"}</button>
        </form>
      </div>
      {pendingReview && (
        <div className="notice warn">The Account Manager has not confirmed the title, location or visa yet. Check them before contacting candidates.</div>
      )}
      {ranking?.shortfall && (
        <form action={widenSearch} className="card">
          <Back to={back} />
          <input type="hidden" name="job_id" value={job.id} />
          <div className="card-head">
            <h2>Only {ranking.shortfall.qualified} of {ranking.shortfall.target} qualified candidates</h2>
            <span className="chip warn">S12</span>
          </div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <span className="muted">
              {ranking.shortfall.widened ? "The search was already widened once. " : ""}
              Choose how to widen the search; it runs as soon as you confirm.
            </span>
            {ranking.shortfall.suggestions.title_variants.length > 0 && (
              <fieldset className="row" style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="faint" style={{ marginBottom: 4 }}>Also search these titles</legend>
                {ranking.shortfall.suggestions.title_variants.map((t) => (
                  <label key={t} className="row"><input type="checkbox" name="title_variant" value={t} defaultChecked /> {t}</label>
                ))}
              </fieldset>
            )}
            <div className="row">
              <label className="row"><span className="faint">Location</span>
                <select name="location" defaultValue="">
                  <option value="">Keep the job&apos;s location</option>
                  <option value="state">Anywhere in the state</option>
                  <option value="anywhere">Any location</option>
                </select>
              </label>
              <label className="row"><input type="checkbox" name="include_nice_to_have" /> Also look for candidates with the nice-to-have skills</label>
            </div>
          </div>
          <div className="card-foot"><button className="btn primary" type="submit">Widen the search</button></div>
        </form>
      )}
      {ranking && (
        <section className="card">
          <div className="card-head">
            <h2>Top {ranked.length}</h2>
            <div className="row">
              <span className="faint">Tick candidates, then</span>
              <button className="btn sm primary" type="submit" form="outreach">Draft outreach emails</button>
            </div>
          </div>
          <form id="outreach" action={draftCandidateOutreach}>
            <Back to={back} />
            <input type="hidden" name="job_id" value={job.id} />
            <input type="hidden" name="channel" value="email" />
          </form>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th><span className="sr-only">Select</span></th><th>#</th><th>Candidate</th><th>Score</th><th>Strengths and gaps</th><th>Recruiter verdict</th></tr>
              </thead>
              <tbody>
                {ranked.map((c) => (
                  <tr key={c.candidate_id}>
                    <td><input type="checkbox" name="candidate_id" value={c.candidate_id} form="outreach" aria-label={`Select ${c.full_name}`} /></td>
                    <td className="num">{c.rank}</td>
                    <td><strong>{c.full_name}</strong><span className="sub">{c.location ?? "Location unknown"} · {c.source}</span></td>
                    <td><Score value={c.score} /></td>
                    <td>
                      {c.strengths.map((s) => <span key={s} className="tag have">{s}</span>)}
                      {c.gaps.map((g) => <span key={g} className="tag miss">{g}</span>)}
                    </td>
                    <td>
                      <form action={reviewCandidate} className="row">
                        <Back to={back} />
                        <input type="hidden" name="job_id" value={job.id} />
                        <input type="hidden" name="candidate_id" value={c.candidate_id} />
                        {c.verdict && <Status value={c.verdict} />}
                        {c.verdict !== "qualified" && <button className="btn sm" name="verdict" value="qualified">Qualified</button>}
                        {c.verdict !== "not_qualified" && <button className="btn sm danger" name="verdict" value="not_qualified">Not qualified</button>}
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {excluded.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>Filtered out</h2><span className="faint">hard filters: must-have skills, work authorisation</span></div>
          <ul className="list">
            {excluded.map((c) => <li key={c.candidate_id}><span className="grow">{c.full_name}</span><span className="faint">{c.excluded_reason}</span></li>)}
          </ul>
        </section>
      )}
    </div>
  );
}

async function EngagementTab({ job, back }: { job: Job; back: string }) {
  const data = await apiOrNull<Engagements>(`/jobs/${job.id}/engagements`);
  const subs = await apiOrNull<Submissions>(`/jobs/${job.id}/submissions`);
  const submitted = new Set(subs?.submissions.filter((s) => !["withdrawn", "rejected"].includes(s.status)).map((s) => s.candidate_id));
  if (!data) return <section className="card"><Empty>Engagement is not available for your role.</Empty></section>;
  const rate = data.summary.completion_rate;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Candidates contacted</h2>
        <span className="faint">{data.summary.total} in progress · details complete {rate == null ? "—" : `${Math.round(rate * 100)}%`} (target 90%)</span>
      </div>
      {data.engagements.length === 0 ? <Empty>No outreach yet. Draft it from the ranked candidates.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Candidate</th><th>Status</th><th>Still missing</th><th>Consent</th><th>Updated</th><th>Next step</th></tr></thead>
            <tbody>
              {data.engagements.map((e) => (
                <tr key={e.id}>
                  <td><strong>{e.candidate_name}</strong></td>
                  <td><Status value={e.status} /></td>
                  <td>{e.missing.length ? e.missing.map((m) => <span key={m} className="tag miss">{humanize(m)}</span>) : <span className="faint">—</span>}</td>
                  <td>{e.consent_recorded ? <span className="chip ok">Recorded</span> : <span className="chip">Not yet</span>}</td>
                  <td>{when(e.updated_at)}</td>
                  <td>
                    {["contacted", "accepted", "replied"].includes(e.status) && (
                      // What happened in LinkedIn or by phone (email replies are read automatically).
                      <form action={recordOutcome} className="row" style={{ marginBottom: 6 }}>
                        <Back to={back} />
                        <input type="hidden" name="engagement_id" value={e.id} />
                        <label className="sr-only" htmlFor={`outcome-${e.id}`}>Outcome for {e.candidate_name}</label>
                        <select id={`outcome-${e.id}`} name="outcome" defaultValue="interested">
                          <option value="accepted">Accepted the connection</option>
                          <option value="replied">Replied</option>
                          <option value="interested">Interested (sends the details request)</option>
                          <option value="not_interested">Not interested</option>
                          <option value="do_not_contact">Do not contact</option>
                        </select>
                        <button className="btn sm">Record</button>
                      </form>
                    )}
                    {["interested", "replied", "contacted", "accepted"].includes(e.status) && (
                      <form action={requestDetails}>
                        <Back to={back} />
                        <input type="hidden" name="engagement_id" value={e.id} />
                        <button className="btn sm">Request details</button>
                      </form>
                    )}
                    {e.status === "complete" && !submitted.has(e.candidate_id) && (
                      <form action={prepareSubmission}>
                        <Back to={`/jobs/${job.id}?tab=submissions`} />
                        <input type="hidden" name="job_id" value={job.id} />
                        <input type="hidden" name="candidate_id" value={e.candidate_id} />
                        <button className="btn sm primary">Prepare submission</button>
                      </form>
                    )}
                    {e.status === "complete" && submitted.has(e.candidate_id) && <span className="faint">Submission prepared</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

async function SubmissionsTab({ job, back }: { job: Job; back: string }) {
  const data = await apiOrNull<Submissions>(`/jobs/${job.id}/submissions`);
  if (!data) return <section className="card"><Empty>Submissions are not available for your role.</Empty></section>;
  if (data.submissions.length === 0) return <section className="card"><Empty>No submissions yet. Prepare one once a candidate&apos;s details and consent are complete.</Empty></section>;
  return (
    <div className="stack">
      {data.submissions.map((s) => (
        <section key={s.id} className="card">
          <div className="card-head">
            <h2>{s.package.candidate_name ?? "Candidate"}</h2>
            <div className="row">
              {s.match && <Score value={s.match.score} />}
              <Status value={s.status} />
            </div>
          </div>
          <div className="card-body stack">
            {s.match && (
              <>
                <p style={{ margin: 0 }}>{s.match.summary}</p>
                <div>
                  {s.match.strengths.map((x) => <span key={x} className="tag have">{x}</span>)}
                  {s.match.concerns.map((x) => <span key={x} className="tag miss">{x}</span>)}
                  {s.match.missing_skills.map((x) => <span key={x} className="tag miss">Missing {x}</span>)}
                </div>
                {s.match.interview_probability != null && (
                  <span className="faint">Estimated interview probability {Math.round(s.match.interview_probability * 100)}%</span>
                )}
              </>
            )}
            {[...(s.match?.flags ?? []), ...s.warnings].map((w) => <div key={w} className="notice warn">{w}</div>)}
            {s.rejection_reason && <div className="notice warn">Rejected at approval: {s.rejection_reason}</div>}
            {s.client_decision && <div className="notice">Client decision: {humanize(s.client_decision)}{s.client_feedback ? ` · ${s.client_feedback}` : ""}</div>}
            <div className="row">
              <span className="faint">Package:</span>
              <a className="btn sm" href={`/files/submissions/${s.id}/package?format=pdf`}>PDF</a>
              <a className="btn sm" href={`/files/submissions/${s.id}/package?format=docx`}>Word</a>
              {s.sent_at && <span className="faint">Sent {day(s.sent_at)} to {s.recipient}</span>}
              {s.status === "pending_approval" && s.approval_request_id && (
                <Link className="btn sm primary" href={`/approvals?id=${s.approval_request_id}`}>Open in approval queue</Link>
              )}
            </div>
          </div>
          {s.status === "matched" && (
            <form action={requestSubmissionApproval} className="card-foot" style={{ justifyContent: "space-between" }}>
              <Back to={back} />
              <input type="hidden" name="submission_id" value={s.id} />
              <div className="stack" style={{ gap: 6 }}>
                <label className="row"><input type="checkbox" name="right_to_represent" required /> I confirm CBS LLC has the candidate&apos;s right to represent them for this job</label>
                {(s.match?.flags.length ?? 0) > 0 && (
                  <label className="row"><input type="checkbox" name="acknowledge_flags" required /> I have read the flags above</label>
                )}
              </div>
              <button className="btn primary" type="submit">Send for approval</button>
            </form>
          )}
        </section>
      ))}
    </div>
  );
}
