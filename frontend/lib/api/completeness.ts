import { api } from "@/lib/http";
import type { ProfileCompleteness } from "@/types/completeness";

export function getProfileCompleteness(): Promise<ProfileCompleteness> {
  return api.get<ProfileCompleteness>("/profile/completeness");
}
