import Link from "next/link";
import { redirect } from "next/navigation";
import { type Search, Notice, PageHead, Status, param } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { GATE_LABEL, kpiValue, when } from "@/lib/format";
import type { KpiReport, Me, Operations } from "@/lib/types";

const FUNNEL: [string, string][] = [
  ["companies", "Target companies"],
  ["first_client_emails", "First emails sent"],
  ["client_replies", "Client replies"],
  ["jobs", "Job descriptions"],
  ["candidates_contacted", "Candidates contacted"],
  ["candidates_replied", "Candidates replied"],
  ["submissions", "Submitted to clients"],
  ["interviews", "Interviews"],
  ["offers", "Offers"],
  ["started", "Started"],
];

const PERIODS = [7, 30, 90];

export default async function Dashboard({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  if (me.role === "client") redirect("/portal");
  const days = PERIODS.includes(Number(param(search, "days"))) ? Number(param(search, "days")) : 30;
  const report = await api<KpiReport>("/kpis", { query: { days } });
  const ops = me.role === "administrator" ? await apiOrNull<Operations>("/admin/operations") : null;
  const top = Math.max(1, ...Object.values(report.funnel));

  return (
    <>
      <PageHead
        title="Dashboard"
        sub={`BRD section 12 KPIs for the last ${days} days, to ${when(report.to)}`}
      >
        {PERIODS.map((d) => (
          <Link key={d} href={`/?days=${d}`} className={d === days ? "btn sm primary" : "btn sm"}>
            {d} days
          </Link>
        ))}
      </PageHead>
      <Notice search={search} />

      {ops && ops.alerts.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head"><h2>Needs attention</h2><span className="chip warn">{ops.alerts.length}</span></div>
          <ul className="list">
            {ops.alerts.map((a) => <li key={a}>{a}</li>)}
          </ul>
        </div>
      )}

      <section className="grid cols-3" aria-label="Key performance indicators" style={{ marginBottom: 16 }}>
        {report.kpis.map((k) => (
          <div key={k.key} className="card kpi">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="label">{k.label}</span>
              <Status value={k.status} />
            </div>
            <div className="value num">{kpiValue(k.value, k.unit)}</div>
            <div className="target">
              Target {k.target}
              {k.unit !== "ratio" && k.unit !== "hours" && k.value !== null ? ` · ${k.unit}` : ""}
            </div>
            <div className="faint" style={{ marginTop: 4 }}>
              {Object.entries(k.basis).map(([b, n]) => `${n} ${b.replace(/_/g, " ")}`).join(" · ")}
            </div>
          </div>
        ))}
      </section>

      <div className={ops ? "split" : undefined}>
        <section className="card" aria-labelledby="funnel">
          <div className="card-head"><h2 id="funnel">Pipeline</h2></div>
          <div className="card-body funnel">
            {FUNNEL.map(([key, label]) => (
              <div key={key} className="funnel-row">
                <span>{label}</span>
                <span className="track"><span style={{ width: `${((report.funnel[key] ?? 0) / top) * 100}%` }} /></span>
                <span className="num" style={{ textAlign: "right" }}>{report.funnel[key] ?? 0}</span>
              </div>
            ))}
          </div>
        </section>

        {ops && (
          <section className="card" aria-labelledby="ops">
            <div className="card-head"><h2 id="ops">Operations</h2><span className="faint">last 30 days</span></div>
            <div className="card-body">
              <dl className="kv">
                <dt>Emails sent</dt>
                <dd className="num">{ops.email_30d.sent} ({ops.email_30d.failed} failed, {ops.email_30d.bounced} bounced)</dd>
                <dt>Approvals waiting</dt>
                <dd>
                  {ops.approvals_pending.total}
                  {Object.entries(ops.approvals_pending.by_gate).map(([g, n]) => (
                    <span key={g} className="sub faint">{n} × {GATE_LABEL[g] ?? g}</span>
                  ))}
                </dd>
                <dt>Follow-ups</dt>
                <dd>{ops.follow_ups.open} open, {ops.follow_ups.overdue} overdue</dd>
                <dt>AI usage</dt>
                <dd className="num">{ops.llm_30d.calls} calls, ${ops.llm_30d.cost_usd.toFixed(2)}</dd>
              </dl>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
