"use client";

import { useEffect, useState } from "react";

import { getProfileCompleteness } from "@/lib/api/completeness";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import type { ProfileCompleteness as ProfileCompletenessData } from "@/types/completeness";

export function ProfileCompleteness() {
  const [data, setData] = useState<ProfileCompletenessData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    getProfileCompleteness()
      .then((result) => {
        if (isMounted) setData(result);
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof ApiError ? err.message : "Unable to load profile completeness.");
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading profile completeness...</p>;
  }

  if (error || !data) {
    return <Alert variant="error">{error ?? "Unable to load profile completeness."}</Alert>;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium text-slate-700">Profile completeness</span>
        <span className="text-sm font-semibold text-slate-900">{data.percentage}%</span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={data.percentage}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-2 w-full overflow-hidden rounded-full bg-slate-100"
      >
        <div
          className="h-full rounded-full bg-brand-600 transition-all"
          style={{ width: `${data.percentage}%` }}
        />
      </div>
      <p className="text-sm text-slate-500">{data.message}</p>
    </div>
  );
}
