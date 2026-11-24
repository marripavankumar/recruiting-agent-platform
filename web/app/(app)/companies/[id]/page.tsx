import type { Metadata } from "next";
import Link from "next/link";
import { checkHiringSignals, draftClientEmails } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Status } from "@/app/components";
import { api } from "@/lib/api";
import { day, humanize, when } from "@/lib/format";
import type { Me } from "@/lib/types";

export const metadata: Metadata = { title: "Company" };

type Contact = {
  id: string;
  full_name: string;
  title: string | null;
  role: string | null;
  email: string | null;
  linkedin_url: string | null;
  country: string;
  consent_basis: string;
  email_valid: boolean;
};
type Posting = { id: string; title: string; target_role: string | null; url: string; location: string | null; date_posted: string | null; last_seen: string | null };
type Company = {
  id: string;
  name: string;
  domain: string;
  industry: string | null;
  revenue_usd: number | null;
  country: string;
  locations: string[];
  website: string | null;
  career_page_url: string | null;
  outreach_status: string;
  priority_score: number;
  is_client: boolean;
  hiring_history: { at: string; open_target_roles: number; roles: string[] }[];
  last_verified_at: string | null;
  sources: string[];
  jd_promised_at: string | null;
  contacts: Contact[];
  postings: Posting[];
};

export default async function CompanyPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = await params;
  const search = await searchParams;
  const me = await api<Me>("/me");
  const c = await api<Company>(`/companies/${encodeURIComponent(id)}`);
  const operator = me.role === "administrator" || me.role === "bd_manager";
  const back = `/companies/${c.id}`;
  return (
    <>
      <PageHead title={c.name} crumbs={<><Link href="/companies">Companies</Link> / {c.domain}</>} sub={[c.industry, c.locations.join("; "), c.country].filter(Boolean).join(" · ")}>
        {c.is_client ? <span className="chip brand">Client</span> : <Status value={c.outreach_status} />}
        <span className={c.priority_score >= 70 ? "chip ok" : "chip warn"}>Priority {c.priority_score}</span>
      </PageHead>
      <Notice search={search} />
      {c.jd_promised_at && <div className="notice" style={{ marginBottom: 16 }}>The client said a JD will follow ({day(c.jd_promised_at)}). Agent 12 reminds the owner if it does not arrive.</div>}

      <div className="split">
        <div className="stack">
          <section className="card">
            <div className="card-head"><h2>Open roles</h2><span className="faint">from the career page, checked {when(c.last_verified_at)}</span></div>
            {c.postings.length === 0 ? <Empty>No open target roles found.</Empty> : (
              <ul className="list">
                {c.postings.map((p) => (
                  <li key={p.id}>
                    <div className="grow">
                      <a href={p.url} target="_blank" rel="noreferrer noopener" className="title">{p.title}</a>
                      <span className="sub faint" style={{ display: "block" }}>{p.target_role ? `Target role: ${p.target_role}` : "Not a target role"} · posted {day(p.date_posted)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <div className="card-head"><h2>Contacts</h2></div>
            {c.contacts.length === 0 ? <Empty>No contacts.</Empty> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Name</th><th>Role</th><th>Email</th><th>Consent (CASL)</th></tr></thead>
                  <tbody>
                    {c.contacts.map((p) => (
                      <tr key={p.id}>
                        <td><strong>{p.full_name}</strong><span className="sub">{p.title}</span></td>
                        <td>{humanize(p.role)}</td>
                        <td>{p.email ?? "—"}{p.email && !p.email_valid && <span className="chip bad" style={{ marginLeft: 6 }}>Bounced</span>}</td>
                        <td>{p.country === "CA" ? <Status value={p.consent_basis === "none" ? "no_consent" : p.consent_basis} /> : <span className="faint">Not needed (US)</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <div className="stack">
          {operator && (
            <section className="card">
              <div className="card-body stack">
                <form action={checkHiringSignals} className="row" style={{ justifyContent: "space-between" }}>
                  <Back to={back} />
                  <input type="hidden" name="company_id" value={c.id} />
                  <span>Re-read the career page</span>
                  <button className="btn sm" type="submit">Check now</button>
                </form>
                {c.outreach_status === "not_contacted" && (
                  <form action={draftClientEmails} className="row" style={{ justifyContent: "space-between" }}>
                    <Back to={back} />
                    <input type="hidden" name="company_id" value={c.id} />
                    <span>Draft a first email</span>
                    <button className="btn sm primary" type="submit">Draft</button>
                  </form>
                )}
              </div>
            </section>
          )}
          <section className="card">
            <div className="card-head"><h2>Hiring history</h2></div>
            {c.hiring_history.length === 0 ? <Empty>No checks yet.</Empty> : (
              <ul className="list">
                {[...c.hiring_history].reverse().map((h) => (
                  <li key={h.at}><span className="grow">{h.open_target_roles} target roles open<span className="sub faint" style={{ display: "block" }}>{h.roles.join(", ")}</span></span><span className="faint">{day(h.at)}</span></li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <div className="card-body">
              <dl className="kv">
                <dt>Website</dt><dd>{c.website ? <a href={c.website} target="_blank" rel="noreferrer noopener">{c.website}</a> : "—"}</dd>
                <dt>Career page</dt><dd>{c.career_page_url ? <a href={c.career_page_url} target="_blank" rel="noreferrer noopener">Open</a> : "—"}</dd>
                <dt>Sources</dt><dd>{c.sources.join(", ")}</dd>
              </dl>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
