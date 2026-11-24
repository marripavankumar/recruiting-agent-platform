// Sign-in for the web app. Identity lives in an httpOnly cookie read only on the server; the
// browser never holds an API credential.
//
// RAP_WEB_AUTH=dev (default outside production): pick a demo user; the server forwards their
// email in X-Dev-User-Email. The API itself accepts that header only when RAP_ENVIRONMENT=local.
// RAP_WEB_AUTH=oidc: OpenID Connect sign-in (next increment); until then sign-in is refused.

import { cookies } from "next/headers";

export const SESSION_COOKIE = "rap_dev_user";

export type AuthMode = "dev" | "oidc";

export function authMode(): AuthMode {
  const mode = process.env.RAP_WEB_AUTH;
  if (mode === "dev" || mode === "oidc") return mode;
  return process.env.NODE_ENV === "production" ? "oidc" : "dev";
}

/** Headers that identify the signed-in user to the API, or null when nobody is signed in. */
export async function identityHeaders(): Promise<Record<string, string> | null> {
  if (authMode() !== "dev") return null;
  const email = (await cookies()).get(SESSION_COOKIE)?.value;
  return email ? { "X-Dev-User-Email": email } : null;
}

export const DEMO_USERS = [
  { email: "administrator@cbs.example", label: "Administrator" },
  { email: "bd.manager@cbs.example", label: "BD Manager" },
  { email: "recruiter@cbs.example", label: "Recruiter" },
  { email: "account.manager@cbs.example", label: "Account Manager" },
  { email: "sales.manager@cbs.example", label: "Sales Manager" },
  { email: "r.lee@northfield-axle.example", label: "Client (Northfield Axle Systems)" },
];
