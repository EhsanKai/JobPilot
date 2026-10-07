"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { logout as logoutRequest } from "@/lib/api/auth";
import { getCurrentUser } from "@/lib/api/users";
import { clearStoredToken, getStoredToken } from "@/lib/auth";
import type { User } from "@/types/user";

interface UseAuthResult {
  user: User | null;
  isLoading: boolean;
  logout: () => Promise<void>;
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

  const logout = useCallback(async () => {
    // End the session on the server too (V0.13): the token stops working immediately,
    // even if a copy of it exists somewhere. Best effort: the local sign-out always happens.
    try {
      await logoutRequest();
    } catch {
      // Already expired or revoked.
    }
    clearStoredToken();
    router.replace("/login");
  }, [router]);

  return { user, isLoading, logout };
}
