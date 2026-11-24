"use client";

// The one client component: it highlights the current page in the sidebar.

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { NavItem } from "@/lib/nav";

export function NavLinks({ items, counts }: { items: NavItem[]; counts: Record<string, number> }) {
  const path = usePathname();
  const groups = [...new Set(items.map((i) => i.group))];
  return (
    <nav className="nav" aria-label="Main">
      {groups.map((group) => (
        <div key={group}>
          <div className="nav-group">{group}</div>
          {items
            .filter((i) => i.group === group)
            .map((item) => {
              const active = item.href === "/" ? path === "/" : path.startsWith(item.href);
              const count = counts[item.href];
              return (
                <Link key={item.href} href={item.href} className={active ? "active" : undefined} aria-current={active ? "page" : undefined}>
                  {item.label}
                  {count ? <span className="count">{count}</span> : null}
                </Link>
              );
            })}
        </div>
      ))}
    </nav>
  );
}
