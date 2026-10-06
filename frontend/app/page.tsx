import Link from "next/link";

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-white to-slate-50 px-6 text-center">
      <p className="text-sm font-semibold uppercase tracking-wide text-brand-600">JobPilot</p>
      <h1 className="mt-4 max-w-2xl text-4xl font-bold text-slate-900 sm:text-5xl">
        Your AI-powered job application assistant.
      </h1>
      <p className="mt-6 text-lg text-slate-600">
        Search smarter. Match better. Apply faster.
      </p>
      <Link
        href="/register"
        className="mt-8 inline-flex items-center justify-center rounded-lg bg-brand-600 px-6 py-3 text-base font-medium text-white shadow-sm transition-colors hover:bg-brand-700"
      >
        Get Started
      </Link>
    </div>
  );
}
