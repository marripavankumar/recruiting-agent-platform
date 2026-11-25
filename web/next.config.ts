import type { NextConfig } from "next";

// Security headers on every page. The app is never shown inside another site's frame
// (clickjacking on the approve and submit buttons), and the browser must not guess file types
// or leak page addresses (they hold record ids) to other sites.
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  // No form-action: sign-out posts a form that redirects to the sign-in provider's logout page.
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const config: NextConfig = {
  output: "standalone", // small container image (environment plan)
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
