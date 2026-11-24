// OpenID Connect sign-in (authorization code flow with PKCE) against any provider: Microsoft
// Entra ID, Google, Okta, Keycloak, Amazon Cognito...
//
// The web app keeps the ID token in an httpOnly cookie and sends it to the API as a bearer
// token. The API verifies its signature, issuer, audience and expiry on every request
// (backend OidcAuthenticator), so set the API's RAP_OIDC_AUDIENCE to this app's client id.

import { createHash, randomBytes } from "node:crypto";

export type OidcConfig = {
  issuer: string;
  clientId: string;
  clientSecret?: string;
  redirectUri: string;
  postLogoutUri: string;
  scopes: string;
};

type Discovery = {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
};

export function oidcConfig(): OidcConfig | null {
  const issuer = process.env.RAP_OIDC_ISSUER;
  const clientId = process.env.RAP_OIDC_CLIENT_ID;
  const base = process.env.RAP_WEB_BASE_URL;
  if (!issuer || !clientId || !base) return null;
  const root = base.replace(/\/$/, "");
  return {
    issuer: issuer.replace(/\/$/, ""),
    clientId,
    clientSecret: process.env.RAP_OIDC_CLIENT_SECRET || undefined,
    redirectUri: `${root}/auth/callback`,
    postLogoutUri: `${root}/signin`,
    scopes: process.env.RAP_OIDC_SCOPES ?? "openid email profile",
  };
}

let cached: { issuer: string; doc: Discovery; at: number } | null = null;

export async function discover(config: OidcConfig): Promise<Discovery> {
  if (cached && cached.issuer === config.issuer && Date.now() - cached.at < 3_600_000) return cached.doc;
  const response = await fetch(`${config.issuer}/.well-known/openid-configuration`, { cache: "no-store" });
  if (!response.ok) throw new Error(`OIDC discovery failed (${response.status})`);
  const doc = (await response.json()) as Discovery;
  if (doc.issuer.replace(/\/$/, "") !== config.issuer) throw new Error("OIDC issuer mismatch");
  cached = { issuer: config.issuer, doc, at: Date.now() };
  return doc;
}

const b64url = (buffer: Buffer) => buffer.toString("base64url");

export function newLoginState() {
  const verifier = b64url(randomBytes(32));
  return {
    state: b64url(randomBytes(16)),
    nonce: b64url(randomBytes(16)),
    verifier,
    challenge: b64url(createHash("sha256").update(verifier).digest()),
  };
}

/** Claims of a JWT, without verifying it (the API verifies; this only reads nonce and exp). */
export function claims(jwt: string): Record<string, unknown> | null {
  const part = jwt.split(".")[1];
  if (!part) return null;
  try {
    return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
  } catch {
    return null;
  }
}

export async function exchangeCode(config: OidcConfig, code: string, verifier: string): Promise<string> {
  const doc = await discover(config);
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: config.redirectUri,
    client_id: config.clientId,
    code_verifier: verifier,
  });
  if (config.clientSecret) body.set("client_secret", config.clientSecret);
  const response = await fetch(doc.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body,
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as { id_token?: string; error?: string; error_description?: string };
  if (!response.ok || !payload.id_token) {
    throw new Error(payload.error_description ?? payload.error ?? `Token exchange failed (${response.status})`);
  }
  return payload.id_token;
}
