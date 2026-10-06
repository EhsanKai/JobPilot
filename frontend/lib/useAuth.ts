"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getCurrentUser } from "@/lib/api/users";
import { clearStoredToken, getStoredToken } from "@/lib/auth";
import type { User } from "@/types/user";

interface UseAuthResult {
  user: User | null;
  isLoading: boolean;
  logout: () => void;
}

/**
 * Loads the current user from the API using the stored token.
 * Redirects to /login if no valid session exists.
 */
export function useRequireAuth(): UseAuthResult {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    async function loadUser() {
      const token = getStoredToken();
      if (!token) {
        router.replace("/login");
        return;
      }

      try {
        const currentUser = await getCurrentUser();
        if (isMounted) {
          setUser(currentUser);
          setIsLoading(false);
        }
      } catch {
        clearStoredToken();
        router.replace("/login");
      }
    }

    loadUser();

    return () => {
      isMounted = false;
    };
  }, [router]);

  const logout = useCallback(() => {
    clearStoredToken();
    router.replace("/login");
  }, [router]);

  return { user, isLoading, logout };
}
