import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";
const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

function originOf(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

/**
 * Content-Security-Policy (V0.13). JobPilot loads no third-party scripts, fonts, analytics or
 * embeds: everything is same-origin except the JobPilot API. Images are same-origin or blob:
 * (the CV photo and the Browser Assistant's live screenshot are fetched with the bearer
 * token and shown as blob: URLs).
 *
 * script-src keeps 'unsafe-inline' because the Next.js App Router inlines its hydration
 * scripts; a nonce-based policy would force every page to render dynamically. 'unsafe-eval'
 * is needed by the development server only.
 */
export function contentSecurityPolicy(api: string = apiUrl, dev: boolean = isDev): string {
  const connect = ["'self'", originOf(api)];
  if (dev) connect.push("ws:"); // Next.js hot reload
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    `connect-src ${connect.filter(Boolean).join(" ")}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

export const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy() },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The Docker image runs the self-contained server (`node server.js`); local `next start`
  // and the E2E suite keep the regular build.
  output: process.env.NEXT_OUTPUT_STANDALONE === "1" ? "standalone" : undefined,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
