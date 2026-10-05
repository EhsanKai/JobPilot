"use client";

import { useEffect, useState } from "react";

import { listResumes } from "@/lib/api/resumes";
import type { Resume } from "@/types/resume";

const STATUS_LABELS: Record<Resume["status"], string> = {
  uploaded: "Not analyzed",
  extracting: "Analyzing",
  extracted: "Analyzing",
  parsing: "Analyzing",
  parsed: "Analyzing",
  needs_review: "Review required",
  confirmed: "Confirmed",
  failed: "Analysis failed",
  ocr_required: "OCR required",
};

/** Older uploads of the user's resume (the current one is shown separately). */
export function ResumeVersions({ currentResumeId }: { currentResumeId: string | null }) {
  const [versions, setVersions] = useState<Resume[]>([]);

  useEffect(() => {
    let isMounted = true;
    listResumes()
      .then((data) => {
        if (isMounted) setVersions(data.filter((resume) => resume.id !== currentResumeId));
      })
      .catch(() => {
        // Non-critical list; leave it empty on failure.
      });
    return () => {
      isMounted = false;
    };
  }, [currentResumeId]);

  if (versions.length === 0) return null;

  return (
    <div>
      <h2 className="text-sm font-semibold text-slate-700">Previous versions</h2>
      <ul className="mt-2 divide-y divide-slate-100 text-sm">
        {versions.map((resume) => (
          <li key={resume.id} className="flex items-center justify-between py-2">
            <span className="text-slate-700">
              Version {resume.version_number} &middot; {resume.file_name}
            </span>
            <span className="text-xs text-slate-500">{STATUS_LABELS[resume.status]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
