"use client";

// Shown when a page cannot load (for example the platform API is down). Details stay in the
// server log; the digest lets the Administrator find them.

export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
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
