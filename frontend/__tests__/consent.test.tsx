import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ConsentBanner } from "@/components/privacy/ConsentBanner";
import { CookiePreferences } from "@/components/privacy/CookiePreferences";
import {
  CONSENT_STORAGE_KEY,
  CONSENT_VERSION,
  OPTIONAL_CATEGORIES,
  hasConsent,
  needsDecision,
  withdrawConsent,
  type ConsentCategoryDefinition,
} from "@/lib/consent";

const ANALYTICS: ConsentCategoryDefinition[] = [
  { key: "analytics", title: "Analytics", description: "Usage statistics." },
  { key: "marketing", title: "Marketing", description: "Advertising." },
];

describe("consent architecture", () => {
  beforeEach(() => window.localStorage.clear());

  it("registers no optional categories: JobPilot uses no non-essential storage", () => {
    expect(OPTIONAL_CATEGORIES).toHaveLength(0);
    expect(needsDecision()).toBe(false);
  });

  it("shows no banner when there is nothing to consent to", () => {
    const { container } = render(<ConsentBanner />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("never reports consent for an unregistered or undecided category", () => {
    expect(hasConsent("analytics")).toBe(false);
    expect(hasConsent("analytics", ANALYTICS)).toBe(false);
  });

  it("asks before anything optional runs, with reject as easy as accept", async () => {
    render(<ConsentBanner categories={ANALYTICS} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const reject = screen.getByRole("button", { name: "Reject all" });
    const accept = screen.getByRole("button", { name: "Accept all" });
    // Equal choices: same component styling, both on the first layer.
    expect(reject.className).toBe(accept.className);

    await userEvent.click(reject);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(hasConsent("analytics", ANALYTICS)).toBe(false);
    expect(hasConsent("marketing", ANALYTICS)).toBe(false);
    const stored = JSON.parse(window.localStorage.getItem(CONSENT_STORAGE_KEY) ?? "{}");
    expect(stored.version).toBe(CONSENT_VERSION);
    expect(typeof stored.decidedAt).toBe("string");
  });

  it("supports granular choices", async () => {
    render(<ConsentBanner categories={ANALYTICS} />);
    await userEvent.click(screen.getByRole("button", { name: "Choose individually" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Analytics/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save my choices" }));
    expect(hasConsent("analytics", ANALYTICS)).toBe(true);
    expect(hasConsent("marketing", ANALYTICS)).toBe(false);
  });

  it("can be withdrawn later, and the user is asked again", async () => {
    render(<ConsentBanner categories={ANALYTICS} />);
    await userEvent.click(screen.getByRole("button", { name: "Accept all" }));
    expect(hasConsent("analytics", ANALYTICS)).toBe(true);

    withdrawConsent();
    expect(hasConsent("analytics", ANALYTICS)).toBe(false);
    expect(needsDecision(ANALYTICS)).toBe(true);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("treats a decision made under an older version as no decision", () => {
    window.localStorage.setItem(
      CONSENT_STORAGE_KEY,
      JSON.stringify({ version: "old", decidedAt: "2020-01-01", granted: { analytics: true, marketing: true } })
    );
    expect(hasConsent("analytics", ANALYTICS)).toBe(false);
    expect(needsDecision(ANALYTICS)).toBe(true);
  });

  it("cookie preferences explain that there is nothing optional today", () => {
    render(<CookiePreferences />);
    expect(screen.getByTestId("no-optional-cookies")).toHaveTextContent("sets no cookies");
    expect(screen.getByText(/Sign-in \(localStorage\)/)).toBeInTheDocument();
  });

  it("cookie preferences change and withdraw a decision", async () => {
    render(<CookiePreferences categories={ANALYTICS} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /Marketing/ }));
    expect(hasConsent("marketing", ANALYTICS)).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Withdraw all optional consent" }));
    expect(hasConsent("marketing", ANALYTICS)).toBe(false);
  });
});
