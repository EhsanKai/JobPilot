"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { getResumeIntelligence, processResume } from "@/lib/api/resumes";
import { ApiError } from "@/lib/http";
import { detectedCountLines, progressLabel } from "@/lib/resumeIntelligence";
import type { Resume } from "@/types/resume";
import type { ResumeIntelligence } from "@/types/resumeProfile";

const DEFAULT_POLL_INTERVAL_MS = 2000;
// Stop polling after ~3 minutes; the user can refresh manually.
const MAX_POLLS = 90;

interface ResumeAnalysisProps {
  resume: Resume;
  onResumeChange?: (resume: Resume) => void;
  pollIntervalMs?: number;
}

export function ResumeAnalysis({
  resume,
  onResumeChange,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: ResumeAnalysisProps) {
  const [intelligence, setIntelligence] = useState<ResumeIntelligence | null>(null);
  const [isLoading, setIsLoading] = useState(resume.status !== "uploaded");
  const [isStarting, setIsStarting] = useState(false);
  const [isPolling, setIsPolling] = useState(false);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [confirmReprocess, setConfirmReprocess] = useState(false);

  const pollCount = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  const onResumeChangeRef = useRef(onResumeChange);
  onResumeChangeRef.current = onResumeChange;

  const refresh = useCallback(async () => {
    const data = await getResumeIntelligence(resume.id);
    if (!mounted.current) return data;
    setIntelligence(data);
    onResumeChangeRef.current?.(data.resume);
    return data;
  }, [resume.id]);

  const poll = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    setIsPolling(true);
    setPollTimedOut(false);
    pollCount.current = 0;

    const tick = async () => {
      if (!mounted.current) return;
      try {
        const data = await refresh();
        pollCount.current += 1;
        if (!data.is_processing) {
          setIsPolling(false);
          return;
        }
        if (pollCount.current >= MAX_POLLS) {
          setIsPolling(false);
          setPollTimedOut(true);
          return;
        }
        timer.current = setTimeout(tick, pollIntervalMs);
      } catch (err) {
        if (!mounted.current) return;
        setIsPolling(false);
        setError(err instanceof ApiError ? err.message : "Unable to check the analysis status.");
      }
    };
    timer.current = setTimeout(tick, pollIntervalMs);
  }, [pollIntervalMs, refresh]);

  useEffect(() => {
    mounted.current = true;
    if (resume.status === "uploaded") {
      setIsLoading(false);
    } else {
      refresh()
        .then((data) => {
          if (data.is_processing) poll();
        })
        .catch((err) => {
          if (mounted.current) {
            setError(err instanceof ApiError ? err.message : "Unable to load the analysis.");
          }
        })
        .finally(() => {
          if (mounted.current) setIsLoading(false);
        });
    }
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
    // Only re-run when a different resume is shown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resume.id]);

  async function startAnalysis(force: boolean) {
    setError(null);
    setInfo(null);
    setConfirmReprocess(false);
    setIsStarting(true);
    try {
      const response = await processResume(resume.id, force);
      onResumeChange?.(response.resume);
      if (response.reused) {
        setInfo(response.message);
        await refresh();
      } else {
        setIntelligence((prev) =>
          prev
            ? { ...prev, resume: response.resume, is_processing: true }
            : {
                resume: response.resume,
                latest_attempt: null,
                is_processing: true,
                profile: null,
              }
        );
        poll();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to start the analysis.");
    } finally {
      setIsStarting(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading resume analysis...</p>;
  }

  const currentResume = intelligence?.resume ?? resume;
  const status = currentResume.status;
  const processing = isPolling || (!pollTimedOut && Boolean(intelligence?.is_processing));
  const profile = intelligence?.profile ?? null;
  const attempt = intelligence?.latest_attempt ?? null;
  const attemptFailed =
    attempt !== null && (attempt.status === "failed" || attempt.status === "ocr_required");

  return (
    <div className="flex flex-col gap-4" data-testid="resume-analysis">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Resume Intelligence</h2>
        <p className="mt-1 text-sm text-slate-500">
          JobPilot extracts your experience, skills, education and languages from your resume.
          Nothing becomes part of your Resume Profile until you review and confirm it.
        </p>
      </div>

      {error && <Alert variant="error">{error}</Alert>}
      {info && <Alert variant="info">{info}</Alert>}

      {processing ? (
        <div className="flex items-center gap-3 rounded-lg border border-slate-200 p-4" role="status">
          <span
            className="h-5 w-5 animate-spin rounded-full border-2 border-brand-600 border-t-transparent"
            aria-hidden="true"
          />
          <div>
            <p className="font-medium text-slate-900">Analyzing your resume...</p>
            <p className="text-sm text-slate-500">
              {progressLabel(status)} This usually takes under a minute.
            </p>
          </div>
        </div>
      ) : (
        <>
          {pollTimedOut && (
            <Alert variant="info">
              The analysis is taking longer than expected. Check back in a few minutes.{" "}
              <button type="button" className="underline" onClick={() => void refresh()}>
                Refresh
              </button>
            </Alert>
          )}

          {attemptFailed && attempt.error_message && (
            <Alert variant="error">
              {profile ? "Re-analysis failed: " : ""}
              {attempt.error_message}
              {profile ? " Your previous analysis is still available." : ""}
            </Alert>
          )}

          {!profile && status !== "ocr_required" && (
            <div>
              <Button onClick={() => void startAnalysis(false)} isLoading={isStarting}>
                {attemptFailed ? "Try again" : "Analyze Resume"}
              </Button>
              {!attemptFailed && (
                <p className="mt-2 text-xs text-slate-400">
                  Your resume text is sent to JobPilot&apos;s AI provider for analysis.
                </p>
              )}
            </div>
          )}

          {!profile && status === "ocr_required" && (
            <p className="text-sm text-slate-500">
              Please upload a text-based PDF or a DOCX version of your resume.
            </p>
          )}

          {profile && (
            <div className="flex flex-col gap-3 rounded-lg border border-slate-200 p-4">
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
              {profile.status === "confirmed" ? (
                <p className="text-sm font-medium text-emerald-700">
                  ✓ Confirmed — this is your trusted Resume Profile.
                </p>
              ) : (
                <p className="text-sm font-medium text-amber-700">
                  ⚠ Review required — please check the extracted information.
                </p>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <Link
                  href="/profile/resume/review"
                  className="inline-flex items-center rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
                >
                  {profile.status === "confirmed" ? "View Resume Profile" : "Review Resume"}
                </Link>
                {!confirmReprocess && (
                  <Button variant="secondary" onClick={() => setConfirmReprocess(true)}>
                    Reprocess Resume
                  </Button>
                )}
              </div>
              {confirmReprocess && (
                <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
                  <p>
                    This analyzes the original file again and replaces the current result. Your
                    review decisions will need to be made again.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <Button onClick={() => void startAnalysis(true)} isLoading={isStarting}>
                      Yes, reprocess
                    </Button>
                    <Button variant="secondary" onClick={() => setConfirmReprocess(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
