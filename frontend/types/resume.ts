export type ResumeStatus =
  | "uploaded"
  | "extracting"
  | "extracted"
  | "parsing"
  | "parsed"
  | "needs_review"
  | "confirmed"
  | "failed"
  | "ocr_required";

export interface Resume {
  id: string;
  user_id: string;
  version_number: number;
  file_name: string;
  mime_type: string;
  file_size: number;
  status: ResumeStatus;
  created_at: string;
  updated_at: string;
}
