export type ResumeStatus = "uploaded" | "processing" | "processed" | "failed";

export interface Resume {
  id: string;
  user_id: string;
  file_name: string;
  mime_type: string;
  file_size: number;
  status: ResumeStatus;
  created_at: string;
  updated_at: string;
}
