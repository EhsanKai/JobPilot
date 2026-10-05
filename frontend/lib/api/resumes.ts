import { api } from "@/lib/http";
import type { Resume } from "@/types/resume";
import type {
  ResumeIntelligence,
  ResumeProcessResponse,
  ResumeProfile,
  ResumeSource,
  ReviewItemRequest,
} from "@/types/resumeProfile";

export function getCurrentResume(): Promise<Resume | null> {
  return api.get<Resume | null>("/resumes/current");
}

export function listResumes(): Promise<Resume[]> {
  return api.get<Resume[]>("/resumes");
}

export function uploadResume(file: File): Promise<Resume> {
  const formData = new FormData();
  formData.append("file", file);
  return api.post<Resume>("/resumes", formData);
}

export function deleteResume(resumeId: string): Promise<void> {
  return api.delete<void>(`/resumes/${resumeId}`);
}

// --- Resume Intelligence (V0.2) ---

export function processResume(resumeId: string, force = false): Promise<ResumeProcessResponse> {
  return api.post<ResumeProcessResponse>(`/resumes/${resumeId}/process`, { force });
}

export function getResumeIntelligence(resumeId: string): Promise<ResumeIntelligence> {
  return api.get<ResumeIntelligence>(`/resumes/${resumeId}/profile`);
}

export function getResumeSource(resumeId: string): Promise<ResumeSource> {
  return api.get<ResumeSource>(`/resumes/${resumeId}/profile/source`);
}

export function reviewResumeItem(
  resumeId: string,
  itemId: string,
  request: ReviewItemRequest
): Promise<ResumeProfile> {
  return api.patch<ResumeProfile>(`/resumes/${resumeId}/profile/items/${itemId}`, request);
}

export function acceptPendingItems(resumeId: string): Promise<ResumeProfile> {
  return api.post<ResumeProfile>(`/resumes/${resumeId}/profile/accept-pending`);
}

export function confirmResumeProfile(resumeId: string): Promise<ResumeProfile> {
  return api.post<ResumeProfile>(`/resumes/${resumeId}/profile/confirm`);
}

export function reopenResumeProfile(resumeId: string): Promise<ResumeProfile> {
  return api.post<ResumeProfile>(`/resumes/${resumeId}/profile/reopen`);
}
