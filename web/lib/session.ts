// Sign-in for the web app. Identity lives in an httpOnly cookie read only on the server; the
// browser never holds an API credential it can read.
//
// RAP_WEB_AUTH=oidc (default in production): company sign-in through OpenID Connect
// (lib/oidc.ts). The ID token goes to the API as a bearer token; the API verifies it.
// RAP_WEB_AUTH=dev (default outside production): pick a demo user; the server forwards their
// email in X-Dev-User-Email. The API accepts that header only when RAP_ENVIRONMENT=local.

import { cookies } from "next/headers";
import { claims } from "./oidc";

export const SESSION_COOKIE = "rap_dev_user";
export const TOKEN_COOKIE = "rap_session";
export const LOGIN_COOKIE = "rap_oidc_login";

export type AuthMode = "dev" | "oidc";

export function authMode(): AuthMode {
  const mode = process.env.RAP_WEB_AUTH;
  if (mode === "dev" || mode === "oidc") return mode;
  return process.env.NODE_ENV === "production" ? "oidc" : "dev";
}

export function secureCookies(): boolean {
  return process.env.NODE_ENV === "production" && !(process.env.RAP_WEB_BASE_URL ?? "").startsWith("http://");
}

/** Headers that identify the signed-in user to the API, or null when nobody is signed in. */
export async function identityHeaders(): Promise<Record<string, string> | null> {
  const jar = await cookies();
  if (authMode() === "dev") {
    const email = jar.get(SESSION_COOKIE)?.value;
    return email ? { "X-Dev-User-Email": email } : null;
  }
  const token = jar.get(TOKEN_COOKIE)?.value;
  if (!token) return null;
  const exp = claims(token)?.exp;
  if (typeof exp === "number" && exp * 1000 < Date.now()) return null; // expired: sign in again
  return { Authorization: `Bearer ${token}` };
}

export const DEMO_USERS = [
  { email: "administrator@cbs.example", label: "Administrator" },
  { email: "bd.manager@cbs.example", label: "BD Manager" },
  { email: "recruiter@cbs.example", label: "Recruiter" },
  { email: "account.manager@cbs.example", label: "Account Manager" },
  { email: "sales.manager@cbs.example", label: "Sales Manager" },
  { email: "r.lee@northfield-axle.example", label: "Client (Northfield Axle Systems)" },
];
