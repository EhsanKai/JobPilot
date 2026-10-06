import { api } from "@/lib/http";
import type { UserLanguage, UserLanguageCreateInput } from "@/types/languages";

export function getLanguages(): Promise<UserLanguage[]> {
  return api.get<UserLanguage[]>("/profile/languages");
}

export function addLanguage(input: UserLanguageCreateInput): Promise<UserLanguage> {
  return api.post<UserLanguage>("/profile/languages", input);
}

export function deleteLanguage(languageId: string): Promise<void> {
  return api.delete<void>(`/profile/languages/${languageId}`);
}
