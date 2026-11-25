import type { Metadata } from "next";
import Link from "next/link";
import { eraseCandidate } from "@/app/actions";
import { Back, type Search, Notice, PageHead, Status } from "@/app/components";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";
import type { CandidateRow } from "../page";

export const metadata: Metadata = { title: "Candidate" };

type Candidate = CandidateRow & {
  phone: string | null;
  preferred_location: string | null;
  notice_period: string | null;
  work_authorization: string | null;
  expected_rate: string | null;
  education: string | null;
  industries: string[];
  projects: string[];
  resume_text: string | null;
  resume_file: { filename: string; size: number; stored_at: string } | null;
};

const DETAILS: [keyof Candidate, string][] = [
  ["email", "Email"],
  ["phone", "Phone"],
  ["linkedin_url", "LinkedIn"],
  ["current_location", "Current location"],
  ["preferred_location", "Preferred location"],
  ["availability", "Availability"],
  ["notice_period", "Notice period"],
  ["visa_status", "Visa status"],
  ["work_authorization", "Work authorisation"],
  ["expected_rate", "Expected rate"],
  ["education", "Education"],
];

export default async function CandidatePage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = await params;
  const search = await searchParams;
  const me = await api<Me>("/me");
  const c = await api<Candidate>(`/candidates/${encodeURIComponent(id)}`);
  const erased = c.consent_status === "withdrawn" && c.email === null && c.resume_text === null;
  return (
    <>
      <PageHead
        title={c.full_name}
        crumbs={<><Link href="/candidates">Candidates</Link> / profile</>}
        sub={[c.experience_years != null ? `${c.experience_years} years of experience` : null, c.current_location, `source: ${c.source}`].filter(Boolean).join(" · ")}
      >
        <Status value={c.consent_status === "none" ? "consent_not_recorded" : `consent_${c.consent_status}`} />
      </PageHead>
      <Notice search={search} />
      <div className="split">
        <section className="card">
          <div className="card-head">
            <h2>Resume</h2>
            {c.resume_file && (
              <a className="btn sm" href={`/files/candidates/${encodeURIComponent(c.id)}/resume`} title={`Stored ${c.resume_file.stored_at.slice(0, 10)}`}>
                Download original ({c.resume_file.filename}, {Math.max(1, Math.round(c.resume_file.size / 1024))} KB)
              </a>
            )}
          </div>
          <div className="card-body">
            {c.resume_text ? <div className="email-body" style={{ padding: 0 }}>{c.resume_text}</div> : <span className="muted">No resume text stored.</span>}
          </div>
        </section>
        <div className="stack">
          <section className="card">
            <div className="card-head"><h2>Details</h2></div>
            <div className="card-body stack">
              <dl className="kv">
                {DETAILS.map(([key, label]) => (
                  <div key={key} style={{ display: "contents" }}>
                    <dt>{label}</dt>
                    <dd>{(c[key] as string | null) ?? <span className="faint">—</span>}</dd>
                  </div>
                ))}
              </dl>
              <div>{c.skills.map((s) => <span key={s} className="tag have">{s}</span>)}</div>
            </div>
          </section>
          {me.role === "administrator" && !erased && (
            <details className="card card-body">
              <summary style={{ cursor: "pointer", fontWeight: 600, color: "var(--bad)" }}>Erase personal data (S23)</summary>
              <form action={eraseCandidate} className="stack" style={{ marginTop: 12 }}>
                <Back to={`/candidates/${c.id}`} />
                <input type="hidden" name="candidate_id" value={c.id} />
                <p className="muted" style={{ margin: 0 }}>
                  Removes the resume, contact details and skills, puts the email and LinkedIn on the do-not-contact list,
                  replaces submission packages with a placeholder and cancels open follow-ups. Approved and sent messages stay
                  in the audit log, as BRD section 10 requires. This cannot be undone.
                </p>
                <label className="stack" style={{ gap: 4 }}>
                  <span className="faint">Reason (kept in the erasure record)</span>
                  <input name="reason" required minLength={3} placeholder="Candidate emailed asking to delete their data" />
                </label>
                <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn danger" type="submit">Erase</button></div>
              </form>
            </details>
          )}
        </div>
      </div>
    </>
  );
}
