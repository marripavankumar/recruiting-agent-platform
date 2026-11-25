import type { Metadata } from "next";
import { signIn } from "@/app/actions";
import { type Search, param } from "@/app/components";
import { oidcConfig } from "@/lib/oidc";
import { DEMO_USERS, authMode } from "@/lib/session";
import { currentTheme } from "@/lib/theme";
import { ThemeToggle } from "@/app/theme-toggle";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignIn({ searchParams }: { searchParams: Promise<Search> }) {
  const search = await searchParams;
  const error = param(search, "error");
  const expired = param(search, "expired");
  const dev = authMode() === "dev";
  const theme = await currentTheme();
  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <div className="card" style={{ width: "min(440px, 100%)" }}>
        <div className="card-head">
          <div className="logo" style={{ color: "var(--text)", padding: 0 }}>
            <span className="logo-mark">CBS</span>
            <span>Recruiting Platform</span>
          </div>
          <ThemeToggle initial={theme} />
        </div>
        <div className="card-body stack">
          {error && <div className="notice warn" role="alert">{error}</div>}
          {expired && !error && <div className="notice">Your session ended. Sign in again.</div>}
          {dev ? (
            <form action={signIn} className="stack">
              <p className="muted" style={{ margin: 0 }}>
                Local demo sign-in. Choose a user to see the platform as that role.
              </p>
              <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 8 }}>
                <legend className="faint" style={{ marginBottom: 6 }}>Sign in as</legend>
                {DEMO_USERS.map((u, i) => (
                  <label key={u.email} className="row" style={{ gap: 10 }}>
                    <input type="radio" name="email" value={u.email} defaultChecked={i === 0} />
                    <span>
                      {u.label}
                      <span className="sub faint" style={{ display: "block" }}>{u.email}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
              <button className="btn primary" type="submit">Sign in</button>
            </form>
          ) : oidcConfig() ? (
            <div className="stack">
              <p className="muted" style={{ margin: 0 }}>Sign in with your company account.</p>
              <a className="btn primary" href="/auth/login" style={{ justifyContent: "center" }}>
                Sign in
              </a>
            </div>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              Company sign-in is not configured for this deployment. The Administrator sets
              RAP_OIDC_ISSUER, RAP_OIDC_CLIENT_ID and RAP_WEB_BASE_URL.
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
