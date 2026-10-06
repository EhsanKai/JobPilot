import { api } from "@/lib/http";
import type { User } from "@/types/user";

export function getCurrentUser(): Promise<User> {
  return api.get<User>("/users/me");
}
