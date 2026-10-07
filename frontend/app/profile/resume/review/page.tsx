"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ResumeReview } from "@/components/resume/ResumeReview";
import { Alert } from "@/components/ui/Alert";
import { getCurrentResume } from "@/lib/api/resumes";
import { ApiError } from "@/lib/http";
import type { Resume } from "@/types/resume";

function CurrentResumeReview() {
  const [resume, setResume] = useState<Resume | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    getCurrentResume()
      .then((data) => {
        if (isMounted) setResume(data);
      })
      .catch((err) => {
        if (isMounted) setError(err instanceof ApiError ? err.message : "Unable to load your resume.");
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (error) return <Alert variant="error">{error}</Alert>;
  if (!resume) {
    return (
      <p className="text-sm text-slate-500">
        You haven&apos;t uploaded a resume yet.{" "}
        <Link href="/profile/resume" className="text-brand-700 underline">
          Upload one
        </Link>
        .
      </p>
    );
  }
  return <ResumeReview resumeId={resume.id} />;
}

export default function ResumeReviewPage() {
  return (
    <AppShell>
      <div className="mb-4">
        <Link href="/profile/resume" className="text-sm text-brand-700 hover:underline">
          ← Back to resume
        </Link>
      </div>
      <CurrentResumeReview />
    </AppShell>
  );
}
