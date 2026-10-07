"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  CONSENT_CHANGED_EVENT,
  OPTIONAL_CATEGORIES,
  acceptAll,
  needsDecision,
  rejectAll,
  saveDecision,
  type ConsentCategoryDefinition,
} from "@/lib/consent";

interface ConsentBannerProps {
  /** Defaults to the registered optional categories (none today: no banner). */
  categories?: readonly ConsentCategoryDefinition[];
}

/**
 * Asks for consent to non-essential storage / services -- only when JobPilot uses any.
 * "Accept all" and "Reject all" are equal choices (same size, same style, same step); the
 * per-category choice is one click away. Nothing optional runs before a decision.
 */
export function ConsentBanner({ categories = OPTIONAL_CATEGORIES }: ConsentBannerProps) {
  const [open, setOpen] = useState(false);
  const [customizing, setCustomizing] = useState(false);
  const [choices, setChoices] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const update = () => setOpen(needsDecision(categories));
    update();
    window.addEventListener(CONSENT_CHANGED_EVENT, update);
    return () => window.removeEventListener(CONSENT_CHANGED_EVENT, update);
  }, [categories]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-labelledby="consent-title"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-slate-200 bg-white p-4 shadow-lg"
    >
      <div className="mx-auto flex max-w-5xl flex-col gap-3">
        <h2 id="consent-title" className="text-sm font-semibold text-slate-900">
          Optional cookies and services
        </h2>
        <p className="text-sm text-slate-600">
          JobPilot works without them. Choose whether to allow the optional ones below; you can
          change this any time in Settings &gt; Privacy.
        </p>
        {customizing && (
          <ul className="flex flex-col gap-2">
            {categories.map((category) => (
              <li key={category.key}>
                <label className="flex items-start gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={choices[category.key] ?? false}
                    onChange={(e) => setChoices({ ...choices, [category.key]: e.target.checked })}
                  />
                  <span>
                    <span className="font-medium">{category.title}</span> — {category.description}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => rejectAll(categories)}>
            Reject all
          </Button>
          <Button variant="secondary" onClick={() => acceptAll(categories)}>
            Accept all
          </Button>
          {customizing ? (
            <Button
              variant="secondary"
              onClick={() =>
                saveDecision(
                  Object.fromEntries(categories.map((c) => [c.key, choices[c.key] ?? false]))
                )
              }
            >
              Save my choices
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => setCustomizing(true)}>
              Choose individually
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
