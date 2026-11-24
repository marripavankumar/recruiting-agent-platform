import type { ReactNode } from "react";
import { signOut } from "@/app/actions";
import { api, apiOrNull } from "@/lib/api";
import { ROLE_LABEL, initials } from "@/lib/format";
import { isStaff, navFor } from "@/lib/nav";
import type { Approval, Me } from "@/lib/types";
import { NavLinks } from "./nav-links";

export const dynamic = "force-dynamic"; // every page shows live data for the signed-in user

export default async function AppLayout({ children }: { children: ReactNode }) {
  const me = await api<Me>("/me");
  const pending = isStaff(me.role) ? await apiOrNull<Approval[]>("/approvals/pending") : null;
  const counts: Record<string, number> = { "/approvals": pending?.length ?? 0 };
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="logo">
          <span className="logo-mark">CBS</span>
          <span>
            Recruiting Platform
            <small>AI agents for engineering staffing</small>
          </span>
        </div>
        <NavLinks items={navFor(me.role)} counts={counts} />
      </aside>
      <div className="main">
        {me.environment === "local" && (
          <div className="mock-banner">Local environment: demo sign-in, nothing is sent outside this machine unless email is configured.</div>
        )}
        <header className="topbar">
          <span className="spacer" />
          <div className="role-switch">
            <span className="avatar" aria-hidden="true">{initials(me.display_name)}</span>
            <span>
              <strong style={{ color: "var(--text)" }}>{me.display_name}</strong>
              <br />
              {ROLE_LABEL[me.role]}
            </span>
          </div>
          <form action={signOut}>
            <button className="btn sm" type="submit">Sign out</button>
          </form>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
