"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  CONSENT_CHANGED_EVENT,
  NECESSARY_STORAGE,
  OPTIONAL_CATEGORIES,
  readDecision,
  saveDecision,
  withdrawConsent,
  type ConsentCategoryDefinition,
  type ConsentDecision,
} from "@/lib/consent";

interface CookiePreferencesProps {
  categories?: readonly ConsentCategoryDefinition[];
}

/** Settings > Privacy > Cookie preferences: what is stored in the browser, and the user's
 *  choices for anything optional (change or withdraw at any time). */
export function CookiePreferences({ categories = OPTIONAL_CATEGORIES }: CookiePreferencesProps) {
  const [decision, setDecision] = useState<ConsentDecision | null>(null);

  useEffect(() => {
    const update = () => setDecision(readDecision(categories));
    update();
    window.addEventListener(CONSENT_CHANGED_EVENT, update);
    return () => window.removeEventListener(CONSENT_CHANGED_EVENT, update);
  }, [categories]);

  return (
    <div className="flex flex-col gap-4 text-sm text-slate-700">
      <div>
        <h3 className="font-medium text-slate-900">Strictly necessary (always on)</h3>
        <ul className="mt-1 list-disc pl-5">
          {NECESSARY_STORAGE.map((item) => (
            <li key={item.key}>
              <span className="font-medium">{item.title}</span>: {item.description}
            </li>
          ))}
        </ul>
      </div>
      {categories.length === 0 ? (
        <p data-testid="no-optional-cookies">
          JobPilot sets no cookies and uses no analytics, tracking, advertising or other optional
          services, so there is nothing to opt in to. If that ever changes, you will be asked first
          and can decide here.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <h3 className="font-medium text-slate-900">Optional</h3>
          {categories.map((category) => (
            <label key={category.key} className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={decision?.granted[category.key] === true}
                onChange={(e) =>
                  saveDecision({
                    ...Object.fromEntries(categories.map((c) => [c.key, false])),
                    ...(decision?.granted ?? {}),
                    [category.key]: e.target.checked,
                  })
                }
              />
              <span>
                <span className="font-medium">{category.title}</span> — {category.description}
              </span>
            </label>
          ))}
          {decision && (
            <p className="text-xs text-slate-500">
              Decided {new Date(decision.decidedAt).toLocaleString()} (version {decision.version}).
            </p>
          )}
          <div>
            <Button variant="secondary" onClick={withdrawConsent}>
              Withdraw all optional consent
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
