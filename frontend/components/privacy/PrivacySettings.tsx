"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { CookiePreferences } from "@/components/privacy/CookiePreferences";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import {
  DELETE_CONFIRMATION_PHRASE,
  deleteMyAccount,
  downloadMyData,
  getConsentHistory,
  getPrivacyOverview,
  getPrivacySettings,
  logoutEverywhere,
  updatePrivacySettings,
} from "@/lib/api/privacy";
import { clearStoredToken } from "@/lib/auth";
import { ApiError } from "@/lib/http";
import type { ConsentRecord, PrivacyOverview, PrivacySettings as Settings } from "@/types/privacy";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function total(records: Record<string, number>): number {
  return Object.values(records).reduce((sum, n) => sum + n, 0);
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <Card aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <h2 id={`${id}-title`} className="text-base font-semibold text-slate-900">
        {title}
      </h2>
      {children}
    </Card>
  );
}

export function PrivacySettings() {
  const router = useRouter();
  const [overview, setOverview] = useState<PrivacyOverview | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [history, setHistory] = useState<ConsentRecord[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [aiSaving, setAiSaving] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([getPrivacyOverview(), getPrivacySettings(), getConsentHistory()])
      .then(([o, s, h]) => {
        if (!active) return;
        setOverview(o);
        setSettings(s);
        setHistory(h);
      })
      .catch((error) => active && setLoadError(errorMessage(error, "Could not load your privacy settings.")));
    return () => {
      active = false;
    };
  }, []);

  async function toggleAi(allowed: boolean) {
    setAiSaving(true);
    setAiError(null);
    try {
      setSettings(await updatePrivacySettings(allowed));
      setHistory(await getConsentHistory());
    } catch (error) {
      setAiError(errorMessage(error, "Could not save your choice."));
    } finally {
      setAiSaving(false);
    }
  }

  async function exportData() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await downloadMyData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `jobpilot-data-${new Date().toISOString().slice(0, 10)}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setExportError(errorMessage(error, "The download failed. Please try again."));
    } finally {
      setExporting(false);
    }
  }

  async function endAllSessions() {
    try {
      await logoutEverywhere();
    } catch {
      // Already signed out on the server: nothing left to end.
    }
    clearStoredToken();
    router.replace("/login");
  }

  async function deleteAccount(event: React.FormEvent) {
    event.preventDefault();
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteMyAccount(password, phrase);
      clearStoredToken();
      router.replace("/login?account=deleted");
    } catch (error) {
      setDeleteError(errorMessage(error, "Your account could not be deleted. Please try again."));
      setDeleting(false);
    }
  }

  if (loadError) return <Alert variant="error">{loadError}</Alert>;
  if (!overview || !settings) return <p className="text-sm text-slate-500">Loading...</p>;

  const phraseMatches = phrase.trim() === DELETE_CONFIRMATION_PHRASE;

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-slate-600">{overview.legal_notice}</p>

      <Section id="my-data" title="My data">
        <p className="text-sm text-slate-600">What JobPilot stores about you, why, and for how long.</p>
        <ul className="flex flex-col gap-3">
          {overview.categories.map((category) => (
            <li key={category.key} className="text-sm" data-testid={`data-category-${category.key}`}>
              <div className="flex items-baseline justify-between gap-4">
                <span className="font-medium text-slate-900">{category.title}</span>
                <span className="text-slate-500">{total(category.records)} records</span>
              </div>
              <p className="text-slate-600">{category.purpose}</p>
              <p className="text-xs text-slate-500">Retention: {category.retention}</p>
            </li>
          ))}
        </ul>
        <p className="text-xs text-slate-500">{overview.file_storage}</p>
      </Section>

      <Section id="download" title="Download my data">
        <p className="text-sm text-slate-600">
          A ZIP file with everything above as machine-readable JSON, plus the resume files and photos
          you uploaded.
        </p>
        {exportError && <Alert variant="error">{exportError}</Alert>}
        <div>
          <Button onClick={exportData} isLoading={exporting}>
            Download my data
          </Button>
        </div>
      </Section>

      <Section id="ai-processing" title="External AI processing">
        <p className="text-sm text-slate-600">
          AI features (resume analysis, tailored documents, answer suggestions, form mapping, match
          explanations) send the needed data to the AI provider configured on this server
          {settings.ai_configured ? ` (${settings.ai_provider})` : " (none is configured right now)"}.
          {settings.contact_details_pseudonymized &&
            " E-mail addresses and phone numbers in your resume are replaced by placeholders before it is sent."}{" "}
          Switch it off and JobPilot sends nothing; everything that works without AI keeps working.
        </p>
        {aiError && <Alert variant="error">{aiError}</Alert>}
        <label className="flex items-center gap-2 text-sm font-medium text-slate-900">
          <input
            type="checkbox"
            checked={settings.external_ai_processing_allowed}
            disabled={aiSaving}
            onChange={(e) => toggleAi(e.target.checked)}
          />
          Allow external AI processing
        </label>
        {history.length > 0 && (
          <div className="text-xs text-slate-500">
            <p className="font-medium">Your recorded decisions</p>
            <ul data-testid="consent-history">
              {history.map((record) => (
                <li key={`${record.decided_at}-${record.granted}`}>
                  {new Date(record.decided_at).toLocaleString()}: {record.granted ? "allowed" : "switched off"} (
                  {record.policy_version})
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section id="cookies" title="Cookie preferences">
        <CookiePreferences />
      </Section>

      <Section id="privacy-information" title="Privacy information">
        <div className="flex flex-col gap-3 text-sm text-slate-700">
          <p className="font-medium text-slate-900">Who else receives data</p>
          <ul className="flex flex-col gap-2">
            {overview.recipients.map((recipient) => (
              <li key={recipient.name}>
                <span className="font-medium">{recipient.name}</span> ({recipient.role}): {recipient.data}{" "}
                <span className="text-slate-500">Purpose: {recipient.purpose} {recipient.control}</span>
              </li>
            ))}
          </ul>
          <p>
            Optional tracking:{" "}
            {overview.optional_tracking.length === 0 ? "none (no analytics, no advertising)." : overview.optional_tracking.join(", ")}
          </p>
        </div>
      </Section>

      <Section id="sessions" title="Sign-ins">
        <p className="text-sm text-slate-600">
          Ends your session on every device and browser, including this one.
        </p>
        <div>
          <Button variant="secondary" onClick={endAllSessions}>
            Log out everywhere
          </Button>
        </div>
      </Section>

      <Section id="delete-account" title="Delete my account">
        <div className="text-sm text-slate-700">
          <p>Deleting your account permanently removes:</p>
          <ul className="mt-1 list-disc pl-5">
            <li>your profile, preferences, skills and languages;</li>
            <li>every resume version you uploaded and everything extracted from it;</li>
            <li>your job list, matches, applications, answers, generated CVs and cover letters, your CV photo;</li>
            <li>Browser Assistant records, submission history, privacy decisions and sign-ins.</li>
          </ul>
          <p className="mt-2">
            It cannot be undone. Applications you already sent to employers are not withdrawn — contact
            them directly. Public job advertisements stay, because they are not yours. Consider
            downloading your data first.
          </p>
        </div>
        {!deleteOpen ? (
          <div>
            <Button variant="danger" onClick={() => setDeleteOpen(true)}>
              I want to delete my account
            </Button>
          </div>
        ) : (
          <form onSubmit={deleteAccount} className="flex flex-col gap-3" aria-label="Delete account">
            {deleteError && <Alert variant="error">{deleteError}</Alert>}
            <Input
              label="Your password"
              name="delete-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <Input
              label={`Type ${DELETE_CONFIRMATION_PHRASE} to confirm`}
              name="delete-confirmation"
              autoComplete="off"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              required
            />
            <div className="flex gap-2">
              <Button
                type="submit"
                variant="danger"
                isLoading={deleting}
                disabled={!phraseMatches || password.length === 0}
              >
                Permanently delete my account
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setDeleteOpen(false);
                  setPassword("");
                  setPhrase("");
                  setDeleteError(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}
      </Section>
    </div>
  );
}
