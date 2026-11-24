// Start OIDC sign-in: remember state, nonce and the PKCE verifier, then go to the provider.

import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { discover, newLoginState, oidcConfig } from "@/lib/oidc";
import { LOGIN_COOKIE, authMode, secureCookies } from "@/lib/session";

// Behind a proxy request.url is the internal address; redirect to the public one.
const site = (request: Request) => process.env.RAP_WEB_BASE_URL ?? request.url;

function fail(request: Request, message: string) {
  return NextResponse.redirect(new URL(`/signin?error=${encodeURIComponent(message)}`, site(request)), 303);
}

export async function GET(request: Request) {
  const config = oidcConfig();
  if (authMode() !== "oidc" || !config) return fail(request, "Company sign-in is not configured");
  let authorize: string;
  try {
    authorize = (await discover(config)).authorization_endpoint;
  } catch {
    return fail(request, "The sign-in provider is not reachable");
  }
  const login = newLoginState();
  (await cookies()).set(LOGIN_COOKIE, JSON.stringify({ state: login.state, nonce: login.nonce, verifier: login.verifier }), {
    httpOnly: true,
    sameSite: "lax", // sent on the provider's top-level redirect back to /auth/callback
    secure: secureCookies(),
    path: "/auth",
    maxAge: 600,
  });
  const url = new URL(authorize);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scopes,
    state: login.state,
    nonce: login.nonce,
    code_challenge: login.challenge,
    code_challenge_method: "S256",
  }).toString();
  return NextResponse.redirect(url, 303);
}
