import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { createUser, setCadence, setGatePolicy, setLlmModel, setRankingWeights, updateUser } from "@/app/actions";
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
  ["model", "AI model"],
  ["usage", "AI usage"],
  ["access", "Access log"],
];

const ROLES = Object.keys(ROLE_LABEL) as Role[];
const STAFF_ROLES = ROLES.filter((r) => r !== "client");

type User = { id: string; email: string; display_name: string; role: Role; client_company_id: string | null; active: boolean };
type Gate = { gate: string; first_approver_role: Role; second_approver_role: Role | null };
type Usage = { template: string; model: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
type Tier = "premium" | "basic" | "free";
type LlmModel = { id: string; name: string; tier: Tier; input_per_million: number; output_per_million: number; context_length: number | null; reasoning: boolean };
type LlmModels = {
  provider: string;
  selected: string | null;
  selected_model: LlmModel | null;
  tiers: { tier: Tier; models: LlmModel[] }[];
  catalog_error: string | null;
  premium_input_per_million: number | null;
};
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
      {tab === "model" && <ModelTab back={back} />}
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

const TIER_LABEL: Record<Tier, string> = { premium: "Premium", basic: "Basic", free: "Free" };

function price(perMillion: number): string {
  return `$${perMillion < 0.1 ? perMillion.toFixed(3) : perMillion.toFixed(2)}`;
}

function describe(m: LlmModel): string {
  const cost = m.tier === "free" ? "free" : `${price(m.input_per_million)} in / ${price(m.output_per_million)} out per M tokens`;
  const context = m.context_length ? ` · ${Math.round(m.context_length / 1000)}k context` : "";
  return `${m.name} — ${cost}${context}${m.reasoning ? " · reasoning" : ""}`;
}

async function ModelTab({ back }: { back: string }) {
  const data = await api<LlmModels>("/admin/llm-models");
  if (data.provider !== "openrouter") {
    return (
      <section className="card">
        <div className="card-head"><h2>AI model</h2></div>
        <div className="card-body stack">
          <p>
            {data.provider === "claude"
              ? "The platform uses Claude directly through the Anthropic API; its model is set with RAP_LLM_MODEL."
              : "No AI model is configured: every agent runs its rule-based path."}
          </p>
          <p className="faint">To choose from the OpenRouter models here, set RAP_LLM_PROVIDER=openrouter and RAP_SECRET_OPENROUTER_API_KEY, then restart the API and scheduler.</p>
        </div>
      </section>
    );
  }
  const count = data.tiers.reduce((n, t) => n + t.models.length, 0);
  const threshold = data.premium_input_per_million ?? 1;
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Model in use</h2><span className="faint">OpenRouter. Every AI step and AI agent run uses it from its next call.</span></div>
        <div className="card-body">
          {data.selected_model ? (
            <p><strong>{data.selected_model.name}</strong> <span className="chip">{TIER_LABEL[data.selected_model.tier]}</span> <span className="mono faint">{data.selected_model.id}</span> <span className="faint">{describe(data.selected_model).split(" — ")[1]}</span></p>
          ) : data.selected ? (
            <p><span className="mono">{data.selected}</span> <span className="chip warn">Not in the current list</span> <span className="faint">It may have been withdrawn or lost tool or structured-output support: choose another.</span></p>
          ) : (
            <p><span className="chip warn">None chosen</span> <span className="faint">Until a model is chosen, AI steps use their rule-based paths and AI agent runs fail.</span></p>
          )}
        </div>
      </section>

      {data.catalog_error ? (
        <section className="card"><div className="card-body"><span className="chip bad">List unavailable</span> {data.catalog_error}. Try again in a few minutes.</div></section>
      ) : (
        <form action={setLlmModel} className="card">
          <Back to={back} />
          <div className="card-head">
            <h2>Choose a model</h2>
            <span className="faint">{count} models with tool calls and structured output, live from OpenRouter. Premium: ${threshold.toFixed(2)} or more per million input tokens · Basic: less · Free: no charge, rate-limited.</span>
          </div>
          <div className="card-body">
            <label className="stack" style={{ gap: 4 }}>
              <span className="faint">Model</span>
              <select name="model" defaultValue={data.selected ?? ""} required>
                <option value="" disabled>Select a model…</option>
                {data.tiers.filter((t) => t.models.length > 0).map((t) => (
                  <optgroup key={t.tier} label={`${TIER_LABEL[t.tier]} (${t.models.length})`}>
                    {t.models.map((m) => <option key={m.id} value={m.id}>{describe(m)}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>
          </div>
          <div className="card-foot">
            <button className="btn primary" type="submit">Use this model</button>
            <span className="faint">Prompts, including resume text and emails, pass through OpenRouter to the model&apos;s provider. Free models may keep prompts: use paid models for real candidate data.</span>
          </div>
        </form>
      )}
    </div>
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
