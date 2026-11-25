// Light / dark theme choice. Kept in a cookie (not localStorage) so the server renders the chosen
// theme on <html> from the first byte: no flash of the wrong theme and no inline script needed.
import { cookies } from "next/headers";

export const THEME_COOKIE = "rap_theme";
export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

export function parseTheme(value: string | undefined): Theme {
  return value === "light" || value === "dark" ? value : "system";
}

export async function currentTheme(): Promise<Theme> {
  return parseTheme((await cookies()).get(THEME_COOKIE)?.value);
}
