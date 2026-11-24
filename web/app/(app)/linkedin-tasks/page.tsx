import type { Metadata } from "next";
import Link from "next/link";
import { markLinkedInSent } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead } from "@/app/components";
import { api } from "@/lib/api";
import { when } from "@/lib/format";

export const metadata: Metadata = { title: "LinkedIn tasks" };

type Task = {
  approval_request_id: string;
  profile_url: string;
  note: string;
  approved_at: string | null;
  candidate_id: string | null;
  candidate_name: string | null;
  job_id: string | null;
};

export default async function LinkedInTasks({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const tasks = await api<Task[]>("/linkedin-tasks");
  return (
    <>
      <PageHead
        title="LinkedIn tasks"
        sub="Approved connection notes. LinkedIn does not allow automated invitations (Q15): send each one yourself in LinkedIn or LinkedIn Recruiter, then mark it sent."
      />
      <Notice search={search} />
      <section className="card">
        <div className="card-head"><h2>To send</h2><span className="chip brand">{tasks.length}</span></div>
        {tasks.length === 0 ? <Empty>No approved notes waiting. Notes appear here once the recruiter approves them.</Empty> : (
          <ul className="list">
            {tasks.map((t) => (
              <li key={t.approval_request_id} style={{ alignItems: "stretch" }}>
                <div className="grow stack" style={{ gap: 6 }}>
                  <div className="row">
                    <strong>{t.candidate_name ?? "Candidate"}</strong>
                    <a href={t.profile_url.startsWith("http") ? t.profile_url : `https://${t.profile_url}`} target="_blank" rel="noreferrer noopener">Open profile</a>
                    {t.job_id && <Link href={`/jobs/${t.job_id}?tab=engagement`} className="faint">job</Link>}
                    <span className="faint">approved {when(t.approved_at)}</span>
                  </div>
                  <label className="stack" style={{ gap: 4 }}>
                    <span className="faint">Note ({t.note.length} of 300 characters): select and copy it into LinkedIn</span>
                    <textarea readOnly defaultValue={t.note} rows={3} />
                  </label>
                </div>
                <form action={markLinkedInSent} style={{ alignSelf: "center" }}>
                  <Back to="/linkedin-tasks" />
                  <input type="hidden" name="approval_request_id" value={t.approval_request_id} />
                  <button className="btn primary" type="submit">Mark sent</button>
                </form>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
