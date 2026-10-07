"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { fetchScreenshot } from "@/lib/api/automation";
import { safeExternalUrl } from "@/lib/jobs";
import type { AutomationSession } from "@/types/automation";

function release(url: string | null) {
  if (url && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url);
}

interface Props {
  applicationId: string;
  session: AutomationSession;
}

/**
 * What the user sees of the browser. A *visible* browser is a window on the machine
 * that runs JobPilot: the user watches and uses that window. A *headless* one has no
 * window: the user gets a screenshot (taken on request, never stored) and a link to
 * finish in their own browser -- never a pretence of control JobPilot cannot give.
 */
export function LiveView({ applicationId, session }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const previous = useRef<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      release(previous.current);
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchScreenshot(applicationId, session.id);
      if (!mounted.current) return;
      release(previous.current);
      previous.current = next;
      setUrl(next);
    } catch {
      if (mounted.current) setError("No live view is available right now.");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [applicationId, session.id]);

  const applicationPage = safeExternalUrl(session.target_url);

  return (
    <details className="rounded-lg border border-slate-200 p-3 text-sm">
      <summary className="cursor-pointer font-medium text-slate-800">Live view</summary>
      {session.browser_mode === "visible" ? (
        <p className="mt-2 text-slate-600">
          The assistant works in a browser window on the computer that runs JobPilot. Press{" "}
          <strong>Take control</strong> to use that window yourself: nothing is changed while you
          are in control.
        </p>
      ) : (
        <p className="mt-2 text-slate-600">
          This server runs the browser without a window, so you see a picture of it here. To
          fill the form yourself, open the application page in your own browser.
          {applicationPage && (
            <>
              {" "}
              <a
                href={applicationPage}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-brand-700 hover:underline"
              >
                Open application website
              </a>
            </>
          )}
        </p>
      )}
      {session.browser_alive && (
        <div className="mt-2">
          <Button type="button" variant="secondary" isLoading={loading} onClick={() => void refresh()}>
            {url ? "Refresh view" : "Show view"}
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-red-700">
          {error}
        </p>
      )}
      {url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt="What the browser shows now"
          className="mt-2 max-h-96 w-full rounded border border-slate-200 object-contain"
        />
      )}
    </details>
  );
}
