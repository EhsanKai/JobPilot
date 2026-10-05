"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ReviewItemCard } from "@/components/resume/review/ReviewItemCard";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import {
  acceptPendingItems,
  confirmResumeProfile,
  getResumeIntelligence,
  getResumeSource,
  reopenResumeProfile,
  reviewResumeItem,
} from "@/lib/api/resumes";
import { ApiError } from "@/lib/http";
import { SECTION_LABELS, SECTION_ORDER, detectedCountLines } from "@/lib/resumeIntelligence";
import type {
  ResumeIntelligence,
  ResumeProfile,
  ReviewAction,
  ReviewableItem,
  SectionName,
} from "@/types/resumeProfile";

function sectionItems(profile: ResumeProfile, section: SectionName): ReviewableItem[] {
  const value = profile.data[section];
  if (value === null) return [];
  return Array.isArray(value) ? (value as ReviewableItem[]) : [value as ReviewableItem];
}

export function ResumeReview({ resumeId }: { resumeId: string }) {
  const [intelligence, setIntelligence] = useState<ResumeIntelligence | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [isBulkBusy, setIsBulkBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const sourcePromise = useRef<Promise<string> | null>(null);

  useEffect(() => {
    let isMounted = true;
    getResumeIntelligence(resumeId)
      .then((data) => {
        if (isMounted) setIntelligence(data);
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof ApiError ? err.message : "Unable to load the resume analysis.");
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, [resumeId]);

  const loadSource = useCallback(() => {
    if (!sourcePromise.current) {
      sourcePromise.current = getResumeSource(resumeId)
        .then((source) => source.text)
        .catch((err) => {
          sourcePromise.current = null;
          throw err;
        });
    }
    return sourcePromise.current;
  }, [resumeId]);

  const profile = intelligence?.profile ?? null;

  function applyProfile(updated: ResumeProfile) {
    setIntelligence((prev) =>
      prev
        ? {
            ...prev,
            profile: updated,
            resume: {
              ...prev.resume,
              status: updated.status === "confirmed" ? "confirmed" : "needs_review",
            },
          }
        : prev
    );
  }

  async function handleItemAction(
    itemId: string,
    action: ReviewAction,
    changes?: Record<string, unknown>
  ): Promise<boolean> {
    setError(null);
    setSuccessMessage(null);
    setBusyItem(itemId);
    try {
      applyProfile(await reviewResumeItem(resumeId, itemId, { action, changes }));
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save your review.");
      return false;
    } finally {
      setBusyItem(null);
    }
  }

  async function runBulk(action: () => Promise<ResumeProfile>, message: string) {
    setError(null);
    setSuccessMessage(null);
    setIsBulkBusy(true);
    try {
      applyProfile(await action());
      setSuccessMessage(message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsBulkBusy(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading resume analysis...</p>;
  }

  if (!profile) {
    return (
      <div className="flex flex-col gap-4">
        {error && <Alert variant="error">{error}</Alert>}
        <p className="text-sm text-slate-500">
          This resume hasn&apos;t been analyzed yet.{" "}
          <Link href="/profile/resume" className="text-brand-700 underline">
            Go to your resume
          </Link>{" "}
          to start the analysis.
        </p>
      </div>
    );
  }

  const confirmed = profile.status === "confirmed";
  const pending = profile.summary.pending_items;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold text-slate-900">Resume Intelligence</h1>
          <span
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              confirmed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
            }`}
          >
            {confirmed ? "Confirmed" : "Review required"}
          </span>
        </div>
        <p className="text-sm text-slate-500">
          What JobPilot extracted from{" "}
          <span className="font-medium">{intelligence?.resume.file_name}</span> (version{" "}
          {intelligence?.resume.version_number}).
        </p>
      </div>

      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <ul className="flex flex-col gap-1 text-sm text-slate-700">
          {detectedCountLines(profile.summary).map((line) => (
            <li key={line}>
              <span className="text-emerald-600" aria-hidden="true">
                ✓{" "}
              </span>
              {line}
            </li>
          ))}
        </ul>
        {confirmed ? (
          <p className="mt-3 text-sm text-emerald-700">
            You confirmed this profile
            {profile.confirmed_at ? ` on ${new Date(profile.confirmed_at).toLocaleDateString()}` : ""}.
            It is now the trusted Resume Profile JobPilot will use.
          </p>
        ) : (
          <p className="mt-3 text-sm text-slate-600">
            Please review the extracted information. Accept what is correct, edit mistakes, and
            reject anything that doesn&apos;t belong. Nothing is trusted until you confirm.
          </p>
        )}
      </div>

      {profile.conflicts.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-5" data-testid="conflicts">
          <h2 className="text-sm font-semibold text-amber-900">
            Differences from your profile ({profile.conflicts.length})
          </h2>
          <p className="mt-1 text-xs text-amber-800">
            Neither value was changed. If your profile is out of date, update it on the{" "}
            <Link href="/profile" className="underline">
              Profile page
            </Link>
            .
          </p>
          <ul className="mt-3 flex flex-col gap-2 text-sm text-amber-900">
            {profile.conflicts.map((conflict) => (
              <li key={conflict.field}>
                <span className="font-medium">{conflict.label}:</span> your profile says “
                {conflict.profile_value}”, your resume says “{conflict.resume_value}”.
              </li>
            ))}
          </ul>
        </div>
      )}

      {profile.warnings.length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-white p-5">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">
            JobPilot adjusted {profile.warnings.length} thing
            {profile.warnings.length === 1 ? "" : "s"} it couldn&apos;t verify in your resume
          </summary>
          <ul className="mt-3 list-disc pl-5 text-sm text-slate-600">
            {profile.warnings.map((warning, index) => (
              <li key={index}>{warning.message}</li>
            ))}
          </ul>
        </details>
      )}

      {SECTION_ORDER.map((section) => {
        const items = sectionItems(profile, section);
        if (items.length === 0) return null;
        return (
          <section key={section} aria-labelledby={`section-${section}`}>
            <h2 id={`section-${section}`} className="mb-3 text-lg font-semibold text-slate-900">
              {SECTION_LABELS[section]}{" "}
              <span className="text-sm font-normal text-slate-400">({items.length})</span>
            </h2>
            <ul className="flex flex-col gap-3">
              {items.map((item) => (
                <ReviewItemCard
                  key={item.id}
                  section={section}
                  item={item}
                  readOnly={confirmed}
                  isBusy={busyItem === item.id || isBulkBusy}
                  onAction={(action, changes) => handleItemAction(item.id, action, changes)}
                  loadSource={loadSource}
                />
              ))}
            </ul>
          </section>
        );
      })}

      <div className="sticky bottom-0 flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
        {confirmed ? (
          <>
            <p className="text-sm text-slate-600">Need to correct something?</p>
            <Button
              variant="secondary"
              onClick={() =>
                void runBulk(
                  () => reopenResumeProfile(resumeId),
                  "Review reopened. Confirm again when you're done."
                )
              }
              isLoading={isBulkBusy}
            >
              Edit again
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-slate-600">
              {pending > 0
                ? `${pending} item${pending === 1 ? "" : "s"} still need${pending === 1 ? "s" : ""} review.`
                : "All items reviewed."}
            </p>
            <div className="flex flex-wrap gap-2">
              {pending > 0 && (
                <Button
                  variant="secondary"
                  onClick={() =>
                    void runBulk(
                      () => acceptPendingItems(resumeId),
                      "All remaining items accepted."
                    )
                  }
                  isLoading={isBulkBusy}
                >
                  Accept all remaining
                </Button>
              )}
              <Button
                onClick={() =>
                  void runBulk(
                    () => confirmResumeProfile(resumeId),
                    "Resume Profile confirmed."
                  )
                }
                disabled={pending > 0 || isBulkBusy}
              >
                Confirm Resume Profile
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
