import type { Metadata } from "next";
import type { ReactNode } from "react";
import { currentTheme } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "CBS Recruiting Platform", template: "%s · CBS Recruiting Platform" },
  description: "AI agent platform for engineering staffing and client acquisition",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const theme = await currentTheme(); // "system": no attribute, the CSS follows the device
  return (
    <html lang="en" data-theme={theme === "system" ? undefined : theme}>
      <body>{children}</body>
    </html>
  );
}
