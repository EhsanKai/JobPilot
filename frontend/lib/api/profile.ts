import { api } from "@/lib/http";
import type { ProfileUpdateInput, ProfileWithUser } from "@/types/profile";

export function getProfile(): Promise<ProfileWithUser> {
  return api.get<ProfileWithUser>("/profile");
}

export function updateProfile(input: ProfileUpdateInput): Promise<ProfileWithUser> {
  return api.put<ProfileWithUser>("/profile", input);
}
