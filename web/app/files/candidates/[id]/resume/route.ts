// Download a candidate's original resume file, as uploaded. Streams the API's bytes; the API
// checks the role.

import { API_URL } from "@/lib/api";
import { identityHeaders } from "@/lib/session";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const headers = await identityHeaders();
  if (headers === null) return new Response("Sign in first", { status: 401 });
  const upstream = await fetch(`${API_URL}/candidates/${encodeURIComponent(id)}/resume-file`, {
    headers,
    cache: "no-store",
  });
  if (!upstream.ok) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition": upstream.headers.get("content-disposition") ?? "attachment",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
