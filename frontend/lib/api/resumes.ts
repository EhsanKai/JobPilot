import { api } from "@/lib/http";
import type { Resume } from "@/types/resume";

export function getCurrentResume(): Promise<Resume | null> {
  return api.get<Resume | null>("/resumes/current");
}

export function uploadResume(file: File): Promise<Resume> {
  const formData = new FormData();
  formData.append("file", file);
  return api.post<Resume>("/resumes", formData);
}

export function deleteResume(resumeId: string): Promise<void> {
  return api.delete<void>(`/resumes/${resumeId}`);
}
