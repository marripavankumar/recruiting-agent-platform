import type { Metadata } from "next";
import Link from "next/link";
import { Empty, type Search, Notice, PageHead, Status } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { day, humanize } from "@/lib/format";
import type { Job } from "@/lib/types";

export const metadata: Metadata = { title: "Jobs & candidates" };

export default async function Jobs({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const jobs = await api<Job[]>("/jobs");
  const companies = (await apiOrNull<{ id: string; name: string }[]>("/companies")) ?? [];
  const name = new Map(companies.map((c) => [c.id, c.name]));
  return (
    <>
      <PageHead title="Jobs & candidates" sub="Each client JD becomes a job (Agents 4-5); open one to source, engage and submit candidates." />
      <Notice search={search} />
      <section className="card">
        {jobs.length === 0 ? (
          <Empty>No jobs yet. They appear when a client replies with a job description.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Job</th><th>Client</th><th>Status</th><th>Priority</th><th>To confirm</th><th>Received</th></tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td>
                      <Link href={`/jobs/${j.id}`}><strong>{j.title}</strong></Link>
                      <span className="sub">{j.job_code}</span>
                    </td>
                    <td>{name.get(j.client_company_id) ?? <span className="faint">—</span>}</td>
                    <td><Status value={j.status} /></td>
                    <td>{humanize(j.priority)}</td>
                    <td>
                      {j.review_fields.length > 0
                        ? <span className="chip warn">{j.review_fields.length} fields</span>
                        : <span className="chip ok">Confirmed</span>}
                    </td>
                    <td>{day(j.created_at)}</td>
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
