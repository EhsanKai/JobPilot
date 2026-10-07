"use client";

import { useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ResumeAnalysis } from "@/components/resume/ResumeAnalysis";
import { ResumeUpload } from "@/components/resume/ResumeUpload";
import { ResumeVersions } from "@/components/resume/ResumeVersions";
import { Card } from "@/components/ui/Card";
import type { Resume } from "@/types/resume";

export default function ResumePage() {
  const [resume, setResume] = useState<Resume | null>(null);

  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Resume</h1>
      <p className="mt-1 text-slate-500">
        Upload your resume, then let JobPilot analyze it. You review everything before it&apos;s
        used.
      </p>
      <Card className="mt-6">
        <ResumeUpload onResumeChange={setResume} />
      </Card>
      {resume && (
        <Card className="mt-6">
          <ResumeAnalysis key={resume.id} resume={resume} />
        </Card>
      )}
      {resume && <ResumeVersions currentResumeId={resume.id} />}
    </AppShell>
  );
}
