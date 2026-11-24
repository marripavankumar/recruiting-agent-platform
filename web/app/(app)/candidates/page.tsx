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

export default async function Candidates({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const q = (param(search, "q") ?? "").trim().toLowerCase();
  const all = await api<CandidateRow[]>("/candidates", { query: { limit: 1000 } });
  const shown = q
    ? all.filter((c) => [c.full_name, c.current_location ?? "", ...c.skills].some((v) => v.toLowerCase().includes(q)))
    : all;
  const back = q ? `/candidates?q=${encodeURIComponent(q)}` : "/candidates";
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
          <h2>{q ? `${shown.length} of ${all.length}` : all.length} candidates</h2>
          <form method="get" className="row">
            <label className="sr-only" htmlFor="q">Search candidates</label>
            <input id="q" name="q" defaultValue={q} placeholder="Name, skill or location" />
            <button className="btn sm" type="submit">Search</button>
          </form>
        </div>
        {shown.length === 0 ? <Empty>{q ? "No candidate matches." : "No candidates yet. Upload resumes to start."}</Empty> : (
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
