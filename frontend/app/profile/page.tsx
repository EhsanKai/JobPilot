"use client";

import { AppShell } from "@/components/layout/AppShell";
import { ProfileForm } from "@/components/profile/ProfileForm";
import { Card } from "@/components/ui/Card";

export default function ProfilePage() {
  return (
    <AppShell>
      <h1 className="text-2xl font-semibold text-slate-900">Profile</h1>
      <p className="mt-1 text-slate-500">Keep your personal details up to date.</p>
      <Card className="mt-6">
        <ProfileForm />
      </Card>
    </AppShell>
  );
}
