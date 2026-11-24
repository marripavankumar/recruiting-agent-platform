import type { Role } from "./types";

export const ROLE_LABEL: Record<Role, string> = {
  administrator: "Administrator",
  bd_manager: "BD Manager",
  recruiter: "Recruiter",
  account_manager: "Account Manager",
  sales_manager: "Sales Manager",
  client: "Client",
};

export const GATE_LABEL: Record<string, string> = {
  first_client_email: "First client email",
  linkedin_connection_request: "LinkedIn connection request",
  candidate_outreach_email: "Candidate outreach email",
  resume_submission: "Resume submission",
};

export const AGENT_LABEL: Record<string, string> = {
  recruiting: "Recruiting coordinator",
  business_development: "Business development",
  intake: "Intake",
  follow_up: "Follow-up",
};

/** "candidate_outreach" -> "Candidate outreach" */
export function humanize(value: string | null | undefined): string {
  if (!value) return "";
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const dateTime = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const dateOnly = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateTime.format(d);
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateOnly.format(d);
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const minutes = Math.round((now - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

export function kpiValue(value: number | null, unit: string): string {
  if (value === null) return "No data";
  if (unit === "ratio") return `${Math.round(value * 100)}%`;
  if (unit === "hours") return `${value.toFixed(1)} h`;
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function statusChip(status: string): string {
  if (["on_track", "approved", "sent", "done", "complete", "submitted", "started", "qualified"].includes(status))
    return "chip ok";
  if (["below_target", "pending", "pending_approval", "waiting_for_human", "drafted", "details_requested"].includes(status))
    return "chip warn";
  if (["failed", "rejected", "not_qualified", "excluded", "do_not_contact", "draft_rejected"].includes(status))
    return "chip bad";
  if (["running", "contacted", "interested", "replied"].includes(status)) return "chip info";
  return "chip";
}
