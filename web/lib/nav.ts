import type { Role } from "./types";

const STAFF: Role[] = ["administrator", "bd_manager", "recruiter", "account_manager", "sales_manager"];

export type NavItem = { href: string; label: string; roles: Role[]; group: string };

// The sidebar shows only what the role may use; the API enforces the same rules.
export const NAV: NavItem[] = [
  { href: "/", label: "Dashboard", roles: STAFF, group: "Overview" },
  { href: "/approvals", label: "Approval queue", roles: STAFF, group: "Overview" },
  { href: "/follow-ups", label: "Follow-ups", roles: STAFF, group: "Overview" },
  {
    href: "/companies",
    label: "Companies & outreach",
    roles: ["administrator", "bd_manager", "sales_manager", "account_manager"],
    group: "Business development",
  },
  { href: "/jobs", label: "Jobs", roles: STAFF, group: "Recruiting" },
  { href: "/candidates", label: "Candidates", roles: ["administrator", "recruiter", "account_manager"], group: "Recruiting" },
  { href: "/linkedin-tasks", label: "LinkedIn tasks", roles: ["administrator", "recruiter"], group: "Recruiting" },
  { href: "/agents", label: "AI agents", roles: STAFF, group: "Automation" },
  { href: "/admin", label: "Administration", roles: ["administrator"], group: "Automation" },
  { href: "/portal", label: "Your submissions", roles: ["client"], group: "Client portal" },
];

export function navFor(role: Role): NavItem[] {
  return NAV.filter((item) => item.roles.includes(role));
}

export function isStaff(role: Role): boolean {
  return role !== "client";
}

export function can(role: Role, href: string): boolean {
  return NAV.some((item) => item.href === href && item.roles.includes(role));
}
