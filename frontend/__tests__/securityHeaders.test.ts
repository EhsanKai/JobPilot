import { describe, expect, it } from "vitest";

import nextConfig, { contentSecurityPolicy, securityHeaders } from "../next.config";

function directives(csp: string): Map<string, string> {
  return new Map(
    csp.split(";").map((part) => {
      const [name, ...values] = part.trim().split(/\s+/);
      return [name, values.join(" ")] as [string, string];
    })
  );
}

describe("frontend security headers", () => {
  it("applies the headers to every route", async () => {
    const rules = await nextConfig.headers!();
    expect(rules).toEqual([{ source: "/:path*", headers: securityHeaders }]);
    const names = securityHeaders.map((h) => h.key);
    for (const name of [
      "Content-Security-Policy",
      "X-Content-Type-Options",
      "X-Frame-Options",
      "Referrer-Policy",
      "Permissions-Policy",
    ]) {
      expect(names).toContain(name);
    }
    expect(nextConfig.poweredByHeader).toBe(false);
  });

  it("allows only the app itself and the JobPilot API in production", () => {
    const csp = directives(contentSecurityPolicy("https://api.example.com/api/v1", false));
    expect(csp.get("default-src")).toBe("'self'");
    expect(csp.get("connect-src")).toBe("'self' https://api.example.com");
    expect(csp.get("frame-ancestors")).toBe("'none'");
    expect(csp.get("object-src")).toBe("'none'");
    expect(csp.get("form-action")).toBe("'self'");
    // Blob images: the CV photo and the Browser Assistant screenshot.
    expect(csp.get("img-src")).toBe("'self' blob: data:");
    expect(csp.get("script-src")).not.toContain("unsafe-eval");
    // No third-party hosts anywhere.
    expect([...csp.values()].join(" ")).not.toMatch(/https:\/\/(?!api\.example\.com)/);
  });

  it("permits eval and hot reload only in development", () => {
    const csp = directives(contentSecurityPolicy("http://localhost:8000/api/v1", true));
    expect(csp.get("script-src")).toContain("'unsafe-eval'");
    expect(csp.get("connect-src")).toContain("ws:");
  });
});
