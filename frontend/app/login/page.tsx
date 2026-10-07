import Link from "next/link";

import { LoginForm } from "@/components/auth/LoginForm";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";

interface LoginPageProps {
  searchParams: Promise<{ account?: string }>;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { account } = await searchParams;
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
      <Card className="w-full max-w-md">
        <h1 className="text-xl font-semibold text-slate-900">Log in to JobPilot</h1>
        <p className="mt-1 text-sm text-slate-500">Welcome back. Enter your details below.</p>
        {account === "deleted" && (
          <div className="mt-4">
            <Alert variant="success">Your account and all of its data have been deleted.</Alert>
          </div>
        )}
        <div className="mt-6">
          <LoginForm />
        </div>
        <p className="mt-6 text-center text-sm text-slate-500">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="font-medium text-brand-600 hover:text-brand-700">
            Sign up
          </Link>
        </p>
      </Card>
    </div>
  );
}
