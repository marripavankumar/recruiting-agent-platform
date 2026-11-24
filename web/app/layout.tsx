import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "CBS Recruiting Platform", template: "%s · CBS Recruiting Platform" },
  description: "AI agent platform for engineering staffing and client acquisition",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
