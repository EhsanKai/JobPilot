"use client";

import { AppShell } from "@/components/layout/AppShell";
import { ResumeUpload } from "@/components/resume/ResumeUpload";
import { Card } from "@/components/ui/Card";

export default function ResumePage() {
  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Resume</h1>
      <p className="mt-1 text-slate-500">
        Upload your resume. We don&apos;t analyze its contents yet.
      </p>
      <Card className="mt-6">
        <ResumeUpload />
      </Card>
    </AppShell>
  );
}
