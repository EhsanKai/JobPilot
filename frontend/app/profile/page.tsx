"use client";

import { AppShell } from "@/components/layout/AppShell";
import { LanguagesManager } from "@/components/profile/LanguagesManager";
import { ProfileCompleteness } from "@/components/profile/ProfileCompleteness";
import { ProfileForm } from "@/components/profile/ProfileForm";
import { SkillsManager } from "@/components/profile/SkillsManager";
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
    </AppShell>
  );
}
