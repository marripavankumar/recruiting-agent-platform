import type { Metadata } from "next";
import Link from "next/link";
import { checkHiringSignals, draftClientEmails, importCompanies } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Status, param } from "@/app/components";
import { api } from "@/lib/api";
import { day } from "@/lib/format";
import type { Me } from "@/lib/types";

export const metadata: Metadata = { title: "Companies & outreach" };

type Company = {
  id: string;
  name: string;
  domain: string;
  industry: string | null;
  revenue_usd: number | null;
  country: string;
  locations: string[];
  outreach_status: string;
  priority_score: number;
  is_client: boolean;
  last_verified_at: string | null;
  sources: string[];
};

const CSV_HEADER =
  "company,domain,industry,revenue_usd,country,location,career_page_url,contact_name,contact_title,contact_email";

function revenue(usd: number | null): string {
  if (!usd) return "—";
  return usd >= 1e9 ? `$${(usd / 1e9).toFixed(1)}B` : `$${Math.round(usd / 1e6)}M`;
}

export default async function Companies({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  const industry = param(search, "industry");
  const companies = await api<Company[]>("/companies", { query: { industry, limit: 500 } });
  const operator = me.role === "administrator" || me.role === "bd_manager";
  const back = industry ? `/companies?industry=${encodeURIComponent(industry)}` : "/companies";
  const industries = [...new Set(companies.map((c) => c.industry).filter(Boolean))] as string[];

  return (
    <>
      <PageHead
        title="Companies & outreach"
        sub="Target companies (Agent 1), their hiring signals and priority (Agent 2), and first emails (Agent 3). Every email waits for the BD Manager's approval."
      />
      <Notice search={search} />

      {operator && (
        <div className="grid cols-2" style={{ marginBottom: 16 }}>
          <form action={importCompanies} className="card">
            <Back to={back} />
            <div className="card-head"><h2>Import a company list</h2><span className="faint">CSV, e.g. an industry-database export</span></div>
            <div className="card-body stack" style={{ gap: 10 }}>
              <label className="stack" style={{ gap: 4 }}>
                <span className="faint">Source name</span>
                <input name="source_name" placeholder="apollo-export-2026-11" required maxLength={80} />
              </label>
              <label className="stack" style={{ gap: 4 }}>
                <span className="faint">CSV file</span>
                <input type="file" name="file" accept=".csv,text/csv" />
              </label>
              <details>
                <summary className="faint">Or paste rows</summary>
                <textarea name="csv" rows={4} placeholder={CSV_HEADER} style={{ marginTop: 6 }} />
              </details>
              <span className="faint">Columns: <code>{CSV_HEADER}</code>. Companies under USD 100M revenue or outside the USA and Canada are skipped.</span>
            </div>
            <div className="card-foot"><button className="btn primary" type="submit">Import</button></div>
          </form>

          <section className="card">
            <div className="card-head"><h2>Run the agents</h2></div>
            <div className="card-body stack">
              <form action={checkHiringSignals} className="row" style={{ justifyContent: "space-between" }}>
                <Back to={back} />
                <span><strong>Check hiring signals</strong><span className="sub faint" style={{ display: "block" }}>Reads every career page for the 27 target roles and updates the priority scores.</span></span>
                <button className="btn" type="submit">Check now</button>
              </form>
              <form action={draftClientEmails} className="row" style={{ justifyContent: "space-between" }}>
                <Back to={back} />
                <span><strong>Draft first emails</strong><span className="sub faint" style={{ display: "block" }}>For high-priority companies not yet contacted. Opt-outs and CASL are checked.</span></span>
                <button className="btn primary" type="submit">Draft</button>
              </form>
            </div>
          </section>
        </div>
      )}

      <section className="card">
        <div className="card-head">
          <h2>{companies.length} companies</h2>
          <form className="row" method="get">
            <label className="sr-only" htmlFor="industry">Industry</label>
            <select id="industry" name="industry" defaultValue={industry ?? ""}>
              <option value="">All industries</option>
              {industries.sort().map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
            <button className="btn sm" type="submit">Filter</button>
          </form>
        </div>
        {companies.length === 0 ? <Empty>No companies yet. Import a list to start.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Company</th><th>Industry</th><th>Revenue</th><th>Location</th><th>Priority</th><th>Outreach</th><th>Checked</th></tr></thead>
              <tbody>
                {companies.map((c) => (
                  <tr key={c.id}>
                    <td><Link href={`/companies/${c.id}`}><strong>{c.name}</strong></Link><span className="sub">{c.domain}</span></td>
                    <td>{c.industry ?? "—"}</td>
                    <td className="num">{revenue(c.revenue_usd)}</td>
                    <td>{c.locations[0] ?? "—"}, {c.country}</td>
                    <td>
                      <span className={c.priority_score >= 70 ? "chip ok" : c.priority_score >= 40 ? "chip warn" : "chip"}>{c.priority_score}</span>
                    </td>
                    <td>{c.is_client ? <span className="chip brand">Client</span> : <Status value={c.outreach_status} />}</td>
                    <td>{day(c.last_verified_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="faint">Priority 0–100 from posting age, number of open target roles and role fit (Q10). First emails go only to companies at or above the configured minimum.</p>
    </>
  );
}
