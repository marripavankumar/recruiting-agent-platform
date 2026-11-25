// Server-side client for the FastAPI backend. Import only from server components, server
// actions and route handlers: it reads the session cookie.

import { redirect } from "next/navigation";
import { FORBIDDEN_DIGEST } from "./errors";
import { identityHeaders } from "./session";

export const API_URL = (process.env.RAP_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  digest?: string;

  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    // The API refused this role: the error page says so instead of "could not load".
    if (status === 403) this.digest = FORBIDDEN_DIGEST;
  }
}

type Options = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  form?: FormData; // multipart upload (resume files)
  query?: Record<string, string | number | boolean | undefined | null>;
};

function detail(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      // FastAPI validation errors: [{loc, msg}, ...]
      return d
        .map((e) => `${(e.loc ?? []).slice(1).join(".") || "input"}: ${e.msg}`)
        .join("; ");
    }
  }
  return `Request failed (${status})`;
}

/** Call the API as the signed-in user. Not signed in (or session rejected): go to sign-in. */
export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const headers = await identityHeaders();
  if (headers === null) redirect("/signin");
  const url = new URL(API_URL + path);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers: {
        ...headers,
        ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: options.form ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(503, `The platform API is not reachable at ${API_URL}`);
  }
  if (response.status === 401) redirect("/signin?expired=1");
  const text = await response.text();
  const payload = text ? safeJson(text) : null;
  const message = detail(payload, response.status);
  // The session names someone who is not (or no longer) an active platform user.
  if (response.status === 403 && message === "Not a platform user") {
    redirect("/signin?error=" + encodeURIComponent("That account cannot use the platform"));
  }
  if (!response.ok) throw new ApiError(response.status, message);
  return payload as T;
}

/** Like api(), but a 403/404/503 becomes null so a page can show what the role may see. */
export async function apiOrNull<T = unknown>(path: string, options: Options = {}): Promise<T | null> {
  try {
    return await api<T>(path, options);
  } catch (error) {
    if (error instanceof ApiError && [403, 404, 503].includes(error.status)) return null;
    throw error;
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
