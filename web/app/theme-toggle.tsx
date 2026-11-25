"use client";

import { useState } from "react";

// Mirrors lib/theme.ts (that module reads server cookies, so it cannot be imported here).
type Theme = "system" | "light" | "dark";
const COOKIE = "rap_theme";
const YEAR = 60 * 60 * 24 * 365;

const OPTIONS: { value: Theme; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.66-5.66 1.41-1.41M4.93 19.07l1.41-1.41m0-11.32L4.93 4.93m14.14 14.14-1.41-1.41M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z" },
  { value: "dark", label: "Dark", icon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" },
  { value: "system", label: "System", icon: "M4 5h16v11H4zM9 20h6m-3-4v4" },
];

/** Light, dark or follow the device. Applies at once and is remembered for a year. */
export function ThemeToggle({ initial }: { initial: Theme }) {
  const [theme, setTheme] = useState<Theme>(initial);

  function choose(next: Theme) {
    setTheme(next);
    const root = document.documentElement;
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
    const secure = location.protocol === "https:" ? "; secure" : "";
    document.cookie = `${COOKIE}=${next}; path=/; max-age=${YEAR}; samesite=lax${secure}`;
  }

  return (
    <div className="theme-toggle" role="group" aria-label="Colour theme">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={theme === o.value}
          title={`${o.label} theme`}
          onClick={() => choose(o.value)}
        >
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <path d={o.icon} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="sr-only">{o.label}</span>
        </button>
      ))}
    </div>
  );
}
