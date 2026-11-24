// Small presentational pieces shared by the pages (server components, no client JavaScript).

import Link from "next/link";
import type { ReactNode } from "react";
import { humanize, statusChip } from "@/lib/format";

export type Search = Record<string, string | string[] | undefined>;

export function param(search: Search, key: string): string | undefined {
  const value = search[key];
  return Array.isArray(value) ? value[0] : value;
}

/** Success or error message carried back by a server action (?ok= / ?error=). */
export function Notice({ search }: { search: Search }) {
  const ok = param(search, "ok");
  const error = param(search, "error");
  if (!ok && !error) return null;
  return (
    <div className="stack" style={{ gap: 8, marginBottom: 16 }}>
      {ok && <div className="notice ok" role="status">{ok}</div>}
      {error && <div className="notice warn" role="alert">{error}</div>}
    </div>
  );
}

export function PageHead({ title, sub, crumbs, children }: { title: string; sub?: ReactNode; crumbs?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        {crumbs && <div className="crumbs">{crumbs}</div>}
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {children && <div className="row">{children}</div>}
    </div>
  );
}

export function Status({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="faint">—</span>;
  return <span className={statusChip(value)}>{humanize(value)}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="card-body muted">{children}</div>;
}

/** Hidden field telling a server action where to return. */
export function Back({ to }: { to: string }) {
  return <input type="hidden" name="back" value={to} />;
}

export function Tabs({ base, current, tabs }: { base: string; current: string; tabs: [string, string][] }) {
  return (
    <nav className="tabs" aria-label="Sections">
      {tabs.map(([key, label]) => (
        <Link key={key} href={`${base}?tab=${key}`} className={key === current ? "active" : undefined} aria-current={key === current ? "page" : undefined}>
          {label}
        </Link>
      ))}
    </nav>
  );
}

export function Score({ value }: { value: number }) {
  return (
    <span className="score" style={{ ["--v" as string]: value }} aria-label={`Score ${value} of 100`}>
      <span>{value}</span>
    </span>
  );
}
