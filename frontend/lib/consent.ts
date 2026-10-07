/**
 * Consent architecture for non-essential browser storage and third-party services (V0.13).
 *
 * JobPilot currently uses NO cookies, analytics, tracking, marketing pixels, embedded
 * third-party services or other non-essential storage. Its only browser storage is the
 * sign-in token in localStorage, which is strictly necessary for the service the user asked
 * for and therefore not a consent category. So `OPTIONAL_CATEGORIES` is empty and no
 * consent banner is shown -- there is nothing to consent to.
 *
 * The architecture exists so that adding one later cannot bypass consent:
 *
 *   1. register the category in OPTIONAL_CATEGORIES (with a description) and bump
 *      CONSENT_VERSION;
 *   2. load the script / set the storage only behind `hasConsent(category)` (or
 *      <ConsentGate category=...>), which is false until the user opted in under the
 *      current version;
 *   3. the banner then appears automatically, with "Accept all" and "Reject all" as equal
 *      choices and per-category settings; Settings > Privacy > Cookie preferences lets the
 *      user change or withdraw the decision at any time.
 *
 * The decision is stored in this browser only (category, version, timestamp) -- no personal
 * data. A version change invalidates earlier decisions (the user is asked again).
 */

export interface ConsentCategoryDefinition {
  key: string;
  title: string;
  description: string;
}

export interface ConsentDecision {
  version: string;
  decidedAt: string;
  granted: Record<string, boolean>;
}

export const CONSENT_VERSION = "2026-10-v1";
export const CONSENT_STORAGE_KEY = "jobpilot_consent";
export const CONSENT_CHANGED_EVENT = "jobpilot:consent-changed";

/** Non-essential categories in use. Empty: JobPilot uses none today. */
export const OPTIONAL_CATEGORIES: readonly ConsentCategoryDefinition[] = [];

/** Strictly necessary storage: always on, never a consent choice (shown for transparency). */
export const NECESSARY_STORAGE = [
  {
    key: "jobpilot_access_token",
    title: "Sign-in (localStorage)",
    description: "Keeps you signed in. Removed when you log out.",
  },
] as const;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readDecision(categories: readonly ConsentCategoryDefinition[] = OPTIONAL_CATEGORIES): ConsentDecision | null {
  const raw = storage()?.getItem(CONSENT_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as ConsentDecision;
    if (parsed.version !== CONSENT_VERSION || typeof parsed.granted !== "object") return null;
    // A category added after the decision was made has not been decided yet.
    if (categories.some((c) => !(c.key in parsed.granted))) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveDecision(granted: Record<string, boolean>): ConsentDecision {
  const decision: ConsentDecision = {
    version: CONSENT_VERSION,
    decidedAt: new Date().toISOString(),
    granted,
  };
  storage()?.setItem(CONSENT_STORAGE_KEY, JSON.stringify(decision));
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CONSENT_CHANGED_EVENT));
  return decision;
}

export function acceptAll(categories: readonly ConsentCategoryDefinition[] = OPTIONAL_CATEGORIES): ConsentDecision {
  return saveDecision(Object.fromEntries(categories.map((c) => [c.key, true])));
}

export function rejectAll(categories: readonly ConsentCategoryDefinition[] = OPTIONAL_CATEGORIES): ConsentDecision {
  return saveDecision(Object.fromEntries(categories.map((c) => [c.key, false])));
}

/** Withdraw: forget the decision, so nothing optional runs and the user is asked again. */
export function withdrawConsent(): void {
  storage()?.removeItem(CONSENT_STORAGE_KEY);
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CONSENT_CHANGED_EVENT));
}

/** True only for a registered optional category the user opted in to under this version. */
export function hasConsent(
  category: string,
  categories: readonly ConsentCategoryDefinition[] = OPTIONAL_CATEGORIES
): boolean {
  if (!categories.some((c) => c.key === category)) return false;
  return readDecision(categories)?.granted[category] === true;
}

export function needsDecision(categories: readonly ConsentCategoryDefinition[] = OPTIONAL_CATEGORIES): boolean {
  return categories.length > 0 && readDecision(categories) === null;
}
