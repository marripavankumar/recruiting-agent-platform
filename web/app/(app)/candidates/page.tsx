import type { Metadata } from "next";
import Link from "next/link";
import { addResumeText, uploadResumes } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Status, param } from "@/app/components";
import { api } from "@/lib/api";

export const metadata: Metadata = { title: "Candidates" };

export type CandidateRow = {
  id: string;
  full_name: string;
  source: string;
  email: string | null;
  linkedin_url: string | null;
  current_location: string | null;
  skills: string[];
  experience_years: number | null;
  availability: string | null;
  visa_status: string | null;
  consent_status: string;
};

// BRD Agent 6 candidate filters; the API applies them (GET /candidates).
const TEXT_FILTERS: [string, string, string][] = [
  ["title", "Job title", "e.g. Quality Engineer"],
  ["skills", "Skills (all of)", "e.g. PFMEA, APQP"],
  ["industry", "Industry", "e.g. Automotive"],
  ["education", "Education", "e.g. Mechanical"],
  ["location", "Location", "e.g. Detroit or MI"],
  ["visa", "Visa / authorisation", "e.g. US citizen"],
  ["availability", "Availability", "e.g. 2 weeks"],
];
const FILTER_KEYS = ["q", ...TEXT_FILTERS.map(([k]) => k), "min_years", "max_hourly_rate", "has_linkedin", "has_resume"];

export default async function Candidates({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const filters = Object.fromEntries(
    FILTER_KEYS.map((k) => [k, (param(search, k) ?? "").trim()]).filter(([, v]) => v !== ""),
  ) as Record<string, string>;
  const filtered = Object.keys(filters).length > 0;
  const more = Object.keys(filters).some((k) => k !== "q");
  const shown = await api<CandidateRow[]>("/candidates", { query: { ...filters, limit: 1000 } });
  const back = filtered ? `/candidates?${new URLSearchParams(filters)}` : "/candidates";
  return (
    <>
      <PageHead title="Candidates" sub="The Candidate Database. Resumes are parsed, de-duplicated by email, phone and LinkedIn, and indexed for search." />
      <Notice search={search} />

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <form action={uploadResumes} className="card">
          <Back to={back} />
          <div className="card-head"><h2>Upload resumes</h2><span className="faint">PDF, Word (.docx) or text, up to 20 files</span></div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <input type="file" name="files" multiple accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" aria-label="Resume files" />
            <div className="row">
              <label className="row"><span className="faint">Source</span>
                <select name="source" defaultValue="manual">
                  <option value="manual">Manual</option>
                  <option value="ats">Internal ATS</option>
                  <option value="linkedin">LinkedIn</option>
                  <option value="referral">Referral</option>
                  <option value="job_board">Job board</option>
                </select>
              </label>
              <label className="row"><span className="faint">Consent to store</span>
                <select name="consent" defaultValue="none">
                  <option value="none">Not recorded</option>
                  <option value="granted">Granted</option>
                </select>
              </label>
            </div>
          </div>
          <div className="card-foot"><button className="btn primary" type="submit">Upload</button></div>
        </form>

        <form action={addResumeText} className="card">
          <Back to={back} />
          <div className="card-head"><h2>Paste a resume</h2></div>
          <div className="card-body">
            <label className="sr-only" htmlFor="resume_text">Resume text</label>
            <textarea id="resume_text" name="resume_text" rows={5} minLength={20} placeholder="Name, contact details, experience, skills..." />
          </div>
          <div className="card-foot"><button className="btn" type="submit">Add</button></div>
        </form>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>{shown.length} {filtered ? "matching " : ""}candidates</h2>
          {filtered && <Link className="btn sm" href="/candidates">Clear filters</Link>}
        </div>
        <form method="get" className="card-body stack" style={{ gap: 10 }}>
          <div className="row">
            <label className="sr-only" htmlFor="q">Search candidates</label>
            <input id="q" name="q" defaultValue={filters.q ?? ""} placeholder="Name, skill, location or past job title" style={{ flex: 1 }} />
            <button className="btn sm primary" type="submit">Search</button>
          </div>
          <details open={more}>
            <summary className="faint">More filters</summary>
            <div className="grid cols-3" style={{ gap: 10, marginTop: 10 }}>
              {TEXT_FILTERS.map(([key, label, hint]) => (
                <label key={key} className="stack" style={{ gap: 4 }}>
                  <span className="faint">{label}</span>
                  <input name={key} defaultValue={filters[key] ?? ""} placeholder={hint} />
                </label>
              ))}
              <label className="stack" style={{ gap: 4 }}>
                <span className="faint">Years of experience, at least</span>
                <input name="min_years" type="number" min={0} max={60} step={1} defaultValue={filters.min_years ?? ""} />
              </label>
              <label className="stack" style={{ gap: 4 }}>
                <span className="faint">Expected rate, at most ($/hour)</span>
                <input name="max_hourly_rate" type="number" min={1} step={1} defaultValue={filters.max_hourly_rate ?? ""} />
              </label>
              {([["has_linkedin", "LinkedIn profile"], ["has_resume", "Resume"]] as const).map(([key, label]) => (
                <label key={key} className="stack" style={{ gap: 4 }}>
                  <span className="faint">{label}</span>
                  <select name={key} defaultValue={filters[key] ?? ""}>
                    <option value="">Any</option>
                    <option value="true">Has one</option>
                    <option value="false">Missing</option>
                  </select>
                </label>
              ))}
            </div>
            <p className="faint" style={{ margin: "8px 0 0" }}>Annual figures count as hourly over 2,080 hours; candidates who state no rate are left out when a rate is set.</p>
          </details>
        </form>
        {shown.length === 0 ? <Empty>{filtered ? "No candidate matches these filters." : "No candidates yet. Upload resumes to start."}</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Candidate</th><th>Skills</th><th>Experience</th><th>Availability</th><th>Work authorisation</th><th>Consent</th></tr></thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id}>
                    <td><Link href={`/candidates/${c.id}`}><strong>{c.full_name}</strong></Link><span className="sub">{c.current_location ?? "Location unknown"} · {c.source}</span></td>
                    <td>{c.skills.slice(0, 6).map((s) => <span key={s} className="tag">{s}</span>)}{c.skills.length > 6 && <span className="faint">+{c.skills.length - 6}</span>}</td>
                    <td className="num">{c.experience_years != null ? `${c.experience_years} yrs` : "—"}</td>
                    <td>{c.availability ?? "—"}</td>
                    <td>{c.visa_status ?? "—"}</td>
                    <td><Status value={c.consent_status === "none" ? "not_recorded" : c.consent_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
