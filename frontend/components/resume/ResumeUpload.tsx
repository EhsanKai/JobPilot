"use client";

import { ChangeEvent, useEffect, useRef, useState } from "react";

import { deleteResume, getCurrentResume, uploadResume } from "@/lib/api/resumes";
import { ApiError } from "@/lib/http";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import type { Resume } from "@/types/resume";

const ACCEPTED_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
];

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface ResumeUploadProps {
  /** Called whenever the current resume changes (loaded, uploaded, deleted). */
  onResumeChange?: (resume: Resume | null) => void;
}

export function ResumeUpload({ onResumeChange }: ResumeUploadProps = {}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [resume, setResume] = useState<Resume | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const onResumeChangeRef = useRef(onResumeChange);
  onResumeChangeRef.current = onResumeChange;

  useEffect(() => {
    let isMounted = true;
    getCurrentResume()
      .then((data) => {
        if (isMounted) {
          setResume(data);
          onResumeChangeRef.current?.(data);
        }
      })
      .catch((err) => {
        if (isMounted) {
          setError(err instanceof ApiError ? err.message : "Unable to load your resume.");
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setError(null);
    setSuccessMessage(null);

    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError("Only PDF and DOCX files are supported.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setIsUploading(true);
    try {
      const uploaded = await uploadResume(file);
      setResume(uploaded);
      onResumeChange?.(uploaded);
      setSuccessMessage("Resume uploaded successfully.");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Upload failed. Please try again."
      );
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleDelete() {
    if (!resume) return;
    setError(null);
    setSuccessMessage(null);
    setIsDeleting(true);

    try {
      await deleteResume(resume.id);
      // Fall back to the previous version, if any.
      const previous = await getCurrentResume();
      setResume(previous);
      onResumeChange?.(previous);
      setSuccessMessage("Resume deleted.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to delete your resume.");
    } finally {
      setIsDeleting(false);
    }
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading your resume...</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <Alert variant="error">{error}</Alert>}
      {successMessage && <Alert variant="success">{successMessage}</Alert>}

      {resume ? (
        <div className="flex items-center justify-between rounded-lg border border-slate-200 p-4">
          <div>
            <p className="font-medium text-slate-900">{resume.file_name}</p>
            <p className="text-sm text-slate-500">
              Version {resume.version_number} &middot; {formatFileSize(resume.file_size)} &middot;
              Uploaded {new Date(resume.created_at).toLocaleDateString()}
            </p>
          </div>
          <Button variant="danger" onClick={handleDelete} isLoading={isDeleting}>
            {isDeleting ? "Deleting..." : "Delete"}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-slate-500">No resume uploaded yet.</p>
      )}

      <div>
        <label htmlFor="resume-file" className="text-sm font-medium text-slate-700">
          {resume ? "Upload a new version" : "Upload resume"}
        </label>
        <input
          ref={fileInputRef}
          id="resume-file"
          type="file"
          accept=".pdf,.docx"
          onChange={handleFileChange}
          disabled={isUploading}
          className="mt-1 block w-full text-sm text-slate-700 file:mr-4 file:rounded-lg file:border-0 file:bg-brand-50 file:px-4 file:py-2 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
        />
        {isUploading && <p className="mt-2 text-sm text-slate-500">Uploading...</p>}
        <p className="mt-1 text-xs text-slate-400">
          PDF or DOCX, up to 10MB.
          {resume ? " Previous versions are kept; your original file is never modified." : ""}
        </p>
      </div>
    </div>
  );
}
