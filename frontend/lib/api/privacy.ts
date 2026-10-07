import { api, fetchBlob } from "@/lib/http";
import type {
  AccountDeletionResult,
  ConsentRecord,
  PrivacyOverview,
  PrivacySettings,
} from "@/types/privacy";

export const DELETE_CONFIRMATION_PHRASE = "DELETE MY ACCOUNT";

export function getPrivacyOverview(): Promise<PrivacyOverview> {
  return api.get<PrivacyOverview>("/privacy/overview");
}

export function getPrivacySettings(): Promise<PrivacySettings> {
  return api.get<PrivacySettings>("/privacy/settings");
}

export function updatePrivacySettings(externalAiProcessingAllowed: boolean): Promise<PrivacySettings> {
  return api.put<PrivacySettings>("/privacy/settings", {
    external_ai_processing_allowed: externalAiProcessingAllowed,
  });
}

export async function getConsentHistory(): Promise<ConsentRecord[]> {
  const body = await api.get<{ records: ConsentRecord[] }>("/privacy/consents");
  return body.records;
}

/** The ZIP with all of the user's data (JSON) and their uploaded files. */
export async function downloadMyData(): Promise<Blob> {
  const blob = await fetchBlob("/privacy/export");
  if (blob === null) throw new Error("The export is not available.");
  return blob;
}

export function deleteMyAccount(password: string, confirmation: string): Promise<AccountDeletionResult> {
  return api.post<AccountDeletionResult>("/privacy/delete-account", { password, confirmation });
}

export function logoutEverywhere(): Promise<void> {
  return api.post<void>("/auth/logout-all");
}
