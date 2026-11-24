// OIDC callback: check state and nonce, exchange the code (PKCE), keep the ID token.

import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { claims, exchangeCode, oidcConfig } from "@/lib/oidc";
import { LOGIN_COOKIE, TOKEN_COOKIE, authMode, secureCookies } from "@/lib/session";

// Behind a proxy request.url is the internal address; redirect to the public one.
const site = (request: Request) => process.env.RAP_WEB_BASE_URL ?? request.url;

function fail(request: Request, message: string) {
  return NextResponse.redirect(new URL(`/signin?error=${encodeURIComponent(message)}`, site(request)), 303);
}

export async function GET(request: Request) {
  const config = oidcConfig();
  if (authMode() !== "oidc" || !config) return fail(request, "Company sign-in is not configured");
  const params = new URL(request.url).searchParams;
  const jar = await cookies();
  const saved = jar.get(LOGIN_COOKIE)?.value;
  jar.delete({ name: LOGIN_COOKIE, path: "/auth" });

  if (params.get("error")) return fail(request, params.get("error_description") ?? params.get("error")!);
  let login: { state: string; nonce: string; verifier: string };
  try {
    login = JSON.parse(saved ?? "");
  } catch {
    return fail(request, "Sign-in expired. Try again.");
  }
  const code = params.get("code");
  if (!code || params.get("state") !== login.state) return fail(request, "Sign-in could not be verified. Try again.");

  let token: string;
  try {
    token = await exchangeCode(config, code, login.verifier);
  } catch (error) {
    return fail(request, error instanceof Error ? error.message : "Sign-in failed");
  }
  const c = claims(token);
  const audience = Array.isArray(c?.aud) ? c.aud : [c?.aud];
  if (!c || c.nonce !== login.nonce || !audience.includes(config.clientId)) {
    return fail(request, "Sign-in could not be verified. Try again.");
  }
  const exp = typeof c.exp === "number" ? c.exp : Math.floor(Date.now() / 1000) + 3600;
  jar.set(TOKEN_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: secureCookies(),
    path: "/",
    maxAge: Math.max(60, exp - Math.floor(Date.now() / 1000)),
  });
  return NextResponse.redirect(new URL("/", site(request)), 303);
}
