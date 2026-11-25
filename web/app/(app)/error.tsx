"use client";

// Shown when a page cannot load. A page the signed-in role may not use (the API answered 403)
// says so plainly; anything else (for example the platform API is down) offers a retry.
// Details stay in the server log; the digest lets the Administrator find them.

import Link from "next/link";
import { FORBIDDEN_DIGEST } from "@/lib/errors";

export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  if (error.digest === FORBIDDEN_DIGEST) {
    return (
      <section className="card" style={{ maxWidth: 640 }}>
        <div className="card-head"><h2>Not available for your role</h2></div>
        <div className="card-body stack">
          <p className="muted" style={{ margin: 0 }}>
            Your role cannot open this page. The menu shows the pages you can use; if you need this one, ask the
            Administrator.
          </p>
        </div>
        <div className="card-foot">
          <Link className="btn primary" href="/">Go to the dashboard</Link>
        </div>
      </section>
    );
  }
  return (
    <section className="card" style={{ maxWidth: 640 }}>
      <div className="card-head"><h2>This page could not load</h2></div>
      <div className="card-body stack">
        <p className="muted" style={{ margin: 0 }}>
          The platform may be restarting or unreachable. Try again in a moment; if it keeps happening, tell the
          Administrator.
        </p>
        {error.digest && <span className="faint">Reference for the Administrator: {error.digest}</span>}
      </div>
      <div className="card-foot">
        <button className="btn primary" type="button" onClick={() => reset()}>Try again</button>
      </div>
    </section>
  );
}
