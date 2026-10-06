import { api, fetchBlob } from "@/lib/http";
import type {
  CvPhoto,
  DocumentEditRequest,
  DocumentType,
  GeneratedDocument,
} from "@/types/documents";

/** The V0.9 actions on documents: regenerate over edits, Humanize, edit, and the CV photo. */

const ENDPOINT: Record<DocumentType, string> = {
  tailored_cv: "cv",
  cover_letter: "cover-letter",
};

function base(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/documents`;
}

/**
 * Generate a new version when the *current* version holds the user's own edits:
 * the server insists on this acknowledgement. The edited version is not deleted.
 */
export function generateOverEdits(
  applicationId: string,
  type: DocumentType
): Promise<GeneratedDocument> {
  return api.post<GeneratedDocument>(`${base(applicationId)}/${ENDPOINT[type]}`, {
    acknowledge_edited_current: true,
  });
}

/**
 * Reword the current Anschreiben for a more natural tone, as a NEW version. It is
 * truth-validated like any generated letter; on failure nothing is saved.
 */
export function humanizeLetter(
  applicationId: string,
  options: { baseDocumentId?: string; acknowledgeEditedCurrent?: boolean } = {}
): Promise<GeneratedDocument> {
  const body: Record<string, unknown> = {};
  if (options.baseDocumentId) body.base_document_id = options.baseDocumentId;
  if (options.acknowledgeEditedCurrent) body.acknowledge_edited_current = true;
  return api.post<GeneratedDocument>(
    `${base(applicationId)}/cover-letter/humanize`,
    Object.keys(body).length > 0 ? body : undefined
  );
}

/** Save the user's edits of a version as a new version (no AI involved). */
export function editDocument(
  applicationId: string,
  documentId: string,
  edit: DocumentEditRequest
): Promise<GeneratedDocument> {
  return api.post<GeneratedDocument>(
    `${base(applicationId)}/${encodeURIComponent(documentId)}/edit`,
    edit
  );
}

// --- The user's own photo for the CV ---------------------------------------------------------

function photoPath(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/cv-photo`;
}

export function uploadPhoto(applicationId: string, file: File): Promise<CvPhoto> {
  const form = new FormData();
  form.append("file", file);
  return api.put<CvPhoto>(photoPath(applicationId), form);
}

export function removePhoto(applicationId: string): Promise<void> {
  return api.delete<void>(photoPath(applicationId));
}

/** The photo file, or null when there is none. */
export function loadPhoto(applicationId: string): Promise<Blob | null> {
  return fetchBlob(photoPath(applicationId));
}
