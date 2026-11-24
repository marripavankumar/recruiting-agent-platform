import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { createUser, setCadence, setGatePolicy, setRankingWeights, updateUser } from "@/app/actions";
import { Back, Empty, type Search, Notice, PageHead, Tabs, param } from "@/app/components";
import { api, apiOrNull } from "@/lib/api";
import { GATE_LABEL, ROLE_LABEL, WEIGHT_KEYS, humanize, when } from "@/lib/format";
import type { Me, Role } from "@/lib/types";

export const metadata: Metadata = { title: "Administration" };

const TABS: [string, string][] = [
  ["users", "Users & roles"],
  ["gates", "Approval gates"],
  ["ranking", "Ranking weights"],
  ["cadence", "Follow-up cadence"],
  ["usage", "AI usage"],
  ["access", "Access log"],
];

const ROLES = Object.keys(ROLE_LABEL) as Role[];
const STAFF_ROLES = ROLES.filter((r) => r !== "client");

type User = { id: string; email: string; display_name: string; role: Role; client_company_id: string | null; active: boolean };
type Gate = { gate: string; first_approver_role: Role; second_approver_role: Role | null };
type Usage = { template: string; model: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
type Denied = { at: string; status: number; method: string; path: string; user_id: string | null; role: string | null; client_ip: string | null };

export default async function Admin({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const me = await api<Me>("/me");
  if (me.role !== "administrator") redirect("/");
  const tab = TABS.some(([k]) => k === param(search, "tab")) ? param(search, "tab")! : "users";
  const back = `/admin?tab=${tab}`;
  return (
    <>
      <PageHead title="Administration" sub="Changes apply immediately, without a restart, and are recorded in the audit log." />
      <Notice search={search} />
      <Tabs base="/admin" current={tab} tabs={TABS} />
      {tab === "users" && <Users me={me} back={back} />}
      {tab === "gates" && <Gates back={back} />}
      {tab === "ranking" && <Weights back={back} />}
      {tab === "cadence" && <Cadence back={back} />}
      {tab === "usage" && <UsageTab />}
      {tab === "access" && <Access />}
    </>
  );
}

async function Users({ me, back }: { me: Me; back: string }) {
  const users = await api<User[]>("/admin/users");
  const companies = (await apiOrNull<{ id: string; name: string; is_client: boolean }[]>("/companies", { query: { limit: 1000 } })) ?? [];
  const name = new Map(companies.map((c) => [c.id, c.name]));
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>{users.length} users</h2></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {users.map((u) => {
                const self = u.id === me.id;
                return (
                  <tr key={u.id}>
                    <td><strong>{u.display_name}</strong><span className="sub">{u.email}{u.client_company_id ? ` · ${name.get(u.client_company_id) ?? "client company"}` : ""}</span></td>
                    <td>
                      {u.role === "client" || self ? ROLE_LABEL[u.role] : (
                        <form action={updateUser} className="row">
                          <Back to={back} />
                          <input type="hidden" name="user_id" value={u.id} />
                          <label className="sr-only" htmlFor={`role-${u.id}`}>Role for {u.display_name}</label>
                          <select id={`role-${u.id}`} name="role" defaultValue={u.role}>
                            {STAFF_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                          </select>
                          <button className="btn sm" type="submit">Save</button>
                        </form>
                      )}
                    </td>
                    <td>{u.active ? <span className="chip ok">Active</span> : <span className="chip">Deactivated</span>}</td>
                    <td>
                      {!self && (
                        <form action={updateUser}>
                          <Back to={back} />
                          <input type="hidden" name="user_id" value={u.id} />
                          <input type="hidden" name="active" value={u.active ? "false" : "true"} />
                          <button className={u.active ? "btn sm danger" : "btn sm"} type="submit">{u.active ? "Deactivate" : "Reactivate"}</button>
                        </form>
                      )}
                      {self && <span className="faint">You</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <form action={createUser} className="card">
        <Back to={back} />
        <div className="card-head"><h2>Add a user</h2><span className="faint">They sign in with their company account (OIDC) using this email.</span></div>
        <div className="card-body grid cols-2">
          <label className="stack" style={{ gap: 4 }}><span className="faint">Email</span><input type="email" name="email" required /></label>
          <label className="stack" style={{ gap: 4 }}><span className="faint">Name</span><input name="display_name" required maxLength={200} /></label>
          <label className="stack" style={{ gap: 4 }}><span className="faint">Role</span>
            <select name="role" defaultValue="recruiter">
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
          </label>
          <label className="stack" style={{ gap: 4 }}><span className="faint">Client company (Client role only)</span>
            <select name="client_company_id" defaultValue="">
              <option value="">—</option>
              {companies.filter((c) => c.is_client).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        </div>
        <div className="card-foot"><button className="btn primary" type="submit">Add user</button></div>
      </form>
    </div>
  );
}

async function Gates({ back }: { back: string }) {
  const gates = await api<Gate[]>("/admin/gate-policies");
  return (
    <section className="card">
      <div className="card-head"><h2>Who approves at each gate</h2><span className="faint">BRD section 10, Q14. A second approver sees the first approver&apos;s final version.</span></div>
      <ul className="list">
        {gates.map((g) => (
          <li key={g.gate}>
            <form action={setGatePolicy} className="row grow" style={{ justifyContent: "space-between" }}>
              <Back to={back} />
              <input type="hidden" name="gate" value={g.gate} />
              <strong style={{ minWidth: 220 }}>{GATE_LABEL[g.gate] ?? humanize(g.gate)}</strong>
              <label className="row"><span className="faint">First approver</span>
                <select name="first_approver_role" defaultValue={g.first_approver_role}>
                  {STAFF_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                </select>
              </label>
              <label className="row"><span className="faint">Second approver</span>
                <select name="second_approver_role" defaultValue={g.second_approver_role ?? ""}>
                  <option value="">None</option>
                  {STAFF_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                </select>
              </label>
              <button className="btn sm" type="submit">Save</button>
            </form>
          </li>
        ))}
      </ul>
    </section>
  );
}

async function Weights({ back }: { back: string }) {
  const w = await api<Record<string, number>>("/admin/ranking-weights");
  const total = WEIGHT_KEYS.reduce((sum, k) => sum + (w[k] ?? 0), 0);
  return (
    <form action={setRankingWeights} className="card">
      <Back to={back} />
      <div className="card-head"><h2>Candidate ranking weights</h2><span className="faint">Agent 7. Weights are relative; current total {total.toFixed(2)}.</span></div>
      <div className="card-body grid cols-3">
        {WEIGHT_KEYS.map((k) => (
          <label key={k} className="stack" style={{ gap: 4 }}>
            <span className="faint">{humanize(k)}</span>
            <input type="number" name={k} defaultValue={w[k]} min={0} step={0.01} required />
          </label>
        ))}
        <label className="stack" style={{ gap: 4 }}>
          <span className="faint">Minimum must-have coverage (hard filter, 0–1)</span>
          <input type="number" name="min_must_have_coverage" defaultValue={w.min_must_have_coverage} min={0} max={1} step={0.05} required />
        </label>
      </div>
      <div className="card-foot"><button className="btn primary" type="submit">Save weights</button></div>
    </form>
  );
}

async function Cadence({ back }: { back: string }) {
  const cadence = await api<Record<string, number[]>>("/admin/follow-up-cadence");
  return (
    <section className="card">
      <div className="card-head"><h2>Follow-up cadence</h2><span className="faint">Q13. Days after the anchor date for each reminder; negative days are before it (e.g. before a start date).</span></div>
      <ul className="list">
        {Object.entries(cadence).map(([kind, days]) => (
          <li key={kind}>
            <form action={setCadence} className="row grow" style={{ justifyContent: "space-between" }}>
              <Back to={back} />
              <input type="hidden" name="kind" value={kind} />
              <strong style={{ minWidth: 220 }}>{humanize(kind)}</strong>
              <label className="row grow"><span className="sr-only">Days for {humanize(kind)}</span>
                <input name="days" defaultValue={days.join(", ")} required pattern="\s*-?\d+(\s*,\s*-?\d+){0,4}\s*" title="Up to five whole numbers, separated by commas" style={{ flex: 1 }} />
              </label>
              <button className="btn sm" type="submit">Save</button>
            </form>
          </li>
        ))}
      </ul>
    </section>
  );
}

async function UsageTab() {
  const usage = await api<Usage[]>("/admin/llm-usage");
  const total = usage.reduce((s, u) => s + u.cost_usd, 0);
  return (
    <section className="card">
      <div className="card-head"><h2>AI usage by prompt</h2><span className="faint">Total ${total.toFixed(2)}</span></div>
      {usage.length === 0 ? <Empty>No AI calls yet. With no model configured the agents use their rule-based paths, which cost nothing.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Prompt / agent</th><th>Model</th><th>Calls</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr></thead>
            <tbody>
              {usage.map((u) => (
                <tr key={`${u.template}-${u.model}`}>
                  <td className="mono">{u.template}</td><td>{u.model}</td>
                  <td className="num">{u.calls}</td><td className="num">{u.input_tokens.toLocaleString("en-US")}</td>
                  <td className="num">{u.output_tokens.toLocaleString("en-US")}</td><td className="num">${u.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

async function Access() {
  const denied = await api<Denied[]>("/admin/access-denied", { query: { limit: 200 } });
  return (
    <section className="card">
      <div className="card-head"><h2>Refused requests</h2><span className="faint">S26, newest first, since the API last started</span></div>
      {denied.length === 0 ? <Empty>No refused requests.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Status</th><th>Request</th><th>User</th><th>Role</th></tr></thead>
            <tbody>
              {denied.map((d, i) => (
                <tr key={`${d.at}-${i}`}>
                  <td>{when(d.at)}</td>
                  <td><span className={d.status === 401 ? "chip warn" : "chip bad"}>{d.status}</span></td>
                  <td className="mono">{d.method} {d.path}</td>
                  <td>{d.user_id ?? <span className="faint">not signed in</span>}</td>
                  <td>{d.role ? humanize(d.role) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
