import { api } from "@/lib/http";
import type { DocumentList, DocumentType, GeneratedDocument } from "@/types/documents";

const ENDPOINT: Record<DocumentType, string> = {
  tailored_cv: "cv",
  cover_letter: "cover-letter",
};

function base(applicationId: string): string {
  return `/applications/${encodeURIComponent(applicationId)}/documents`;
}

/** Every generation attempt for the application, and what is needed before the next one. */
export function listDocuments(applicationId: string): Promise<DocumentList> {
  return api.get<DocumentList>(base(applicationId));
}

export function getDocument(applicationId: string, documentId: string): Promise<GeneratedDocument> {
  return api.get<GeneratedDocument>(`${base(applicationId)}/${encodeURIComponent(documentId)}`);
}

/**
 * Generate a new version of the CV or letter. It takes no body: language,
 * resume and salary decision are the application's own saved data. Only a
 * document that passed truth validation against the confirmed resume is
 * returned; anything else is an error (see `DOCUMENT_REJECTED`).
 */
export function generateDocument(
  applicationId: string,
  type: DocumentType
): Promise<GeneratedDocument> {
  return api.post<GeneratedDocument>(`${base(applicationId)}/${ENDPOINT[type]}`);
}
