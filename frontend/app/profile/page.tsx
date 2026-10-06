"use client";

import { AppShell } from "@/components/layout/AppShell";
import { LanguagesManager } from "@/components/profile/LanguagesManager";
import { ProfileCompleteness } from "@/components/profile/ProfileCompleteness";
import { ProfileForm } from "@/components/profile/ProfileForm";
import { SkillsManager } from "@/components/profile/SkillsManager";
import { ResumeIntelligenceSummary } from "@/components/resume/ResumeIntelligenceSummary";
import { Card } from "@/components/ui/Card";

export default function ProfilePage() {
  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Profile</h1>
      <p className="mt-1 text-slate-500">
        Keep your job search profile up to date so future versions can match you to the right
        roles.
      </p>

      <Card className="mt-6">
        <ProfileCompleteness />
      </Card>

      <div className="mt-10">
        <h2 className="text-xl font-semibold text-slate-900">Your Profile</h2>
        <p className="mt-1 text-sm text-slate-500">What you tell JobPilot about yourself.</p>
      </div>

      <Card className="mt-6">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">Personal &amp; professional</h2>
        <ProfileForm />
      </Card>

      <Card className="mt-6">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">Skills</h2>
        <SkillsManager />
      </Card>

      <Card className="mt-6">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">Languages</h2>
        <LanguagesManager />
      </Card>

      <div className="mt-10">
        <h2 className="text-xl font-semibold text-slate-900">Resume Intelligence</h2>
        <p className="mt-1 text-sm text-slate-500">
          What JobPilot extracted from your resume. Kept separate from your own profile -- nothing
          here overwrites what you entered above.
        </p>
      </div>

      <Card className="mt-6">
        <ResumeIntelligenceSummary />
      </Card>
    </AppShell>
  );
}
