"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getCurrentResume, getResumeIntelligence } from "@/lib/api/resumes";
import { detectedCountLines } from "@/lib/resumeIntelligence";
import type { Resume } from "@/types/resume";
import type { ResumeProfile } from "@/types/resumeProfile";

/** Read-only snapshot of the Resume Profile for the Profile page. */
export function ResumeIntelligenceSummary() {
  const [resume, setResume] = useState<Resume | null>(null);
  const [profile, setProfile] = useState<ResumeProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    (async () => {
      const current = await getCurrentResume();
      if (!isMounted) return;
      setResume(current);
      if (current && current.status !== "uploaded") {
        const intelligence = await getResumeIntelligence(current.id);
        if (isMounted) setProfile(intelligence.profile);
      }
    })()
      .catch(() => {
        // Non-critical summary; fall back to the empty state.
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>;

  if (!profile) {
    return (
      <p className="text-sm text-slate-500">
        {resume ? "Your resume hasn't been analyzed yet. " : "No resume uploaded yet. "}
        <Link href="/profile/resume" className="text-brand-700 underline">
          {resume ? "Analyze your resume" : "Upload your resume"}
        </Link>
      </p>
    );
  }

  const confirmed = profile.status === "confirmed";
  return (
    <div className="flex flex-col gap-3">
      <p className={`text-sm font-medium ${confirmed ? "text-emerald-700" : "text-amber-700"}`}>
        {confirmed ? "✓ Confirmed Resume Profile" : "⚠ Review required"}
      </p>
      <ul className="flex flex-col gap-1 text-sm text-slate-700">
        {detectedCountLines(profile.summary).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {profile.conflicts.length > 0 && (
        <p className="text-sm text-amber-700">
          {profile.conflicts.length} difference{profile.conflicts.length === 1 ? "" : "s"} between
          your resume and the profile above.
        </p>
      )}
      <Link href="/profile/resume/review" className="text-sm font-medium text-brand-700 hover:underline">
        {confirmed ? "View Resume Profile" : "Review Resume"} →
      </Link>
    </div>
  );
}
