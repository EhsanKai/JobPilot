import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResumeUpload } from "@/components/resume/ResumeUpload";

const getCurrentResumeMock = vi.fn();
const uploadResumeMock = vi.fn();
const deleteResumeMock = vi.fn();
vi.mock("@/lib/api/resumes", () => ({
  getCurrentResume: () => getCurrentResumeMock(),
  uploadResume: (...args: unknown[]) => uploadResumeMock(...args),
  deleteResume: (...args: unknown[]) => deleteResumeMock(...args),
}));

function makeFile(name: string, type: string): File {
  return new File(["dummy content"], name, { type });
}

describe("ResumeUpload", () => {
  beforeEach(() => {
    getCurrentResumeMock.mockReset();
    uploadResumeMock.mockReset();
    deleteResumeMock.mockReset();
  });

  it("shows an empty state when there is no resume", async () => {
    getCurrentResumeMock.mockResolvedValue(null);
    render(<ResumeUpload />);
    expect(await screen.findByText(/no resume uploaded yet/i)).toBeInTheDocument();
  });

  it("uploads a valid PDF and shows a success message", async () => {
    getCurrentResumeMock.mockResolvedValue(null);
    uploadResumeMock.mockResolvedValue({
      id: "resume-1",
      user_id: "user-1",
      file_name: "resume.pdf",
      mime_type: "application/pdf",
      file_size: 1024,
      status: "uploaded",
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });

    const user = userEvent.setup();
    render(<ResumeUpload />);
    await screen.findByText(/no resume uploaded yet/i);

    const file = makeFile("resume.pdf", "application/pdf");
    const input = screen.getByLabelText(/upload resume/i);
    await user.upload(input, file);

    await waitFor(() => expect(uploadResumeMock).toHaveBeenCalled());
    expect(await screen.findByText(/resume uploaded successfully/i)).toBeInTheDocument();
    expect(await screen.findByText("resume.pdf")).toBeInTheDocument();
  });

  it("rejects an unsupported file type without calling the API", async () => {
    getCurrentResumeMock.mockResolvedValue(null);

    render(<ResumeUpload />);
    await screen.findByText(/no resume uploaded yet/i);

    // Bypass the input's `accept` filtering (which userEvent.upload respects,
    // mimicking the native file picker) to simulate a file slipping through,
    // e.g. via drag-and-drop or a renamed extension.
    const file = makeFile("resume.txt", "text/plain");
    const input = screen.getByLabelText(/upload resume/i);
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only PDF and DOCX files are supported."
    );
    expect(uploadResumeMock).not.toHaveBeenCalled();
  });
});
