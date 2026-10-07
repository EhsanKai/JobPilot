import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationDocuments } from "@/components/applications/ApplicationDocuments";
import { ApiError } from "@/lib/http";
import type { DocumentSummary, GeneratedDocument } from "@/types/documents";

import {
  makeCvContent,
  makeDocument,
  makeLetterContent,
  makeList,
  makeSummary,
} from "./fixtures/documents";

const listDocumentsMock = vi.fn();
const getDocumentMock = vi.fn();
const generateDocumentMock = vi.fn();
const humanizeMock = vi.fn();
const editMock = vi.fn();
const overEditsMock = vi.fn();
const uploadPhotoMock = vi.fn();
const removePhotoMock = vi.fn();
const loadPhotoMock = vi.fn();

vi.mock("@/lib/api/documents", () => ({
  listDocuments: (...a: unknown[]) => listDocumentsMock(...a),
  getDocument: (...a: unknown[]) => getDocumentMock(...a),
  generateDocument: (...a: unknown[]) => generateDocumentMock(...a),
}));
vi.mock("@/lib/api/documentActions", () => ({
  humanizeLetter: (...a: unknown[]) => humanizeMock(...a),
  editDocument: (...a: unknown[]) => editMock(...a),
  generateOverEdits: (...a: unknown[]) => overEditsMock(...a),
  uploadPhoto: (...a: unknown[]) => uploadPhotoMock(...a),
  removePhoto: (...a: unknown[]) => removePhotoMock(...a),
  loadPhoto: (...a: unknown[]) => loadPhotoMock(...a),
}));

const LETTER_TITLE = "Cover letter (Anschreiben)";

function letterSummary(version: number, overrides: Partial<DocumentSummary> = {}) {
  return makeSummary({
    id: `letter-v${version}`,
    document_type: "cover_letter",
    version_number: version,
    is_current: true,
    prompt_version: "cover_letter_v1",
    ...overrides,
  });
}

function cvSummary(version: number, overrides: Partial<DocumentSummary> = {}) {
  return makeSummary({
    id: `cv-v${version}`,
    document_type: "tailored_cv",
    version_number: version,
    is_current: true,
    ...overrides,
  });
}

async function renderDocuments(list = makeList()) {
  listDocumentsMock.mockResolvedValue(list);
  render(
    <ApplicationDocuments
      applicationId="app-1"
      language="de"
      reloadKey="t0"
      hasUnsavedChanges={false}
      pollIntervalMs={10}
    />
  );
  await screen.findByRole("heading", { name: "Tailored CV" });
}

function panel(name: string) {
  return within(screen.getByRole("heading", { name }).closest("section")!);
}

const letter = () => panel(LETTER_TITLE);

/** The "✓ Generated · Version N" status of a panel (the history list repeats versions). */
function currentStatus(name: string) {
  return screen
    .getByRole("heading", { name })
    .closest("section")!
    .querySelector('[data-status="generated"]');
}
const cv = () => panel("Tailored CV");

beforeEach(() => {
  [
    listDocumentsMock,
    getDocumentMock,
    generateDocumentMock,
    humanizeMock,
    editMock,
    overEditsMock,
    uploadPhotoMock,
    removePhotoMock,
    loadPhotoMock,
  ].forEach((mock) => mock.mockReset());
});

describe("✨ Humanize", () => {
  it("is offered on the Anschreiben once there is one, and not on the CV", async () => {
    await renderDocuments(makeList([letterSummary(1), cvSummary(1)]));
    expect(letter().getByRole("button", { name: "Humanize cover letter" })).toHaveTextContent("✨ Humanize");
    expect(cv().queryByRole("button", { name: /Humanize/ })).not.toBeInTheDocument();
  });

  it("is not offered when there is no letter to humanize", async () => {
    await renderDocuments(makeList([cvSummary(1)]));
    expect(screen.queryByRole("button", { name: /Humanize/ })).not.toBeInTheDocument();
  });

  it("asks first, and says it creates a new version", async () => {
    await renderDocuments(makeList([letterSummary(1)]));
    const user = userEvent.setup();
    await user.click(letter().getByRole("button", { name: "Humanize cover letter" }));

    const confirm = screen.getByRole("group", { name: "Confirm humanize" });
    expect(confirm).toHaveTextContent("Humanize this cover letter while preserving all factual content?");
    expect(confirm).toHaveTextContent("new version");
    expect(confirm).toHaveTextContent("stays untouched in the history");
    expect(humanizeMock).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Confirm humanize" })).not.toBeInTheDocument();
    expect(humanizeMock).not.toHaveBeenCalled();
  });

  it("shows a loading state, then the new version, and keeps the old one available", async () => {
    const v1 = letterSummary(1);
    await renderDocuments(makeList([v1]));
    const v2 = makeDocument(
      { id: "letter-v2", version_number: 2, origin: "humanized", base_document_id: v1.id },
      "cover_letter"
    );
    let finish: (value: GeneratedDocument) => void = () => {};
    humanizeMock.mockReturnValue(new Promise<GeneratedDocument>((resolve) => (finish = resolve)));
    const user = userEvent.setup();

    await user.click(letter().getByRole("button", { name: "Humanize cover letter" }));
    await user.click(screen.getByRole("button", { name: "Humanize" }));

    expect(humanizeMock).toHaveBeenCalledWith("app-1", { acknowledgeEditedCurrent: false });
    expect(await screen.findByRole("status")).toHaveTextContent(/Humanizing\.\.\./);
    expect(letter().getByRole("button", { name: "Humanize cover letter" })).toBeDisabled();
    expect(letter().getByRole("button", { name: "Regenerate Cover letter (Anschreiben)" })).toBeDisabled();

    // the server now has two versions; the new one is current
    listDocumentsMock.mockResolvedValue(
      makeList([
        letterSummary(2, { id: "letter-v2", origin: "humanized", base_document_id: v1.id }),
        { ...v1, is_current: false },
      ])
    );
    finish(v2);

    const viewer = await screen.findByRole("region", { name: `${LETTER_TITLE}, version 2` });
    expect(viewer).toBeInTheDocument();
    expect(viewer).toHaveTextContent("Reworded by Humanize");
    await waitFor(() => expect(currentStatus(LETTER_TITLE)).toHaveTextContent("Version 2"));
    expect(currentStatus(LETTER_TITLE)).toHaveTextContent("reworded by Humanize");

    await user.click(letter().getByText(/History \(2 attempts\)/));
    expect(letter().getByRole("button", { name: `View ${LETTER_TITLE} version 1` })).toBeEnabled();
    expect(letter().getByRole("button", { name: `View ${LETTER_TITLE} version 2` })).toBeInTheDocument();
    expect(letter().getByText(/humanized/)).toBeInTheDocument();
  });

  it("leaves the current letter untouched and says why when the result is rejected", async () => {
    await renderDocuments(makeList([letterSummary(1)]));
    humanizeMock.mockRejectedValue(
      new ApiError(502, "DOCUMENT_REJECTED", "The reworded letter contained changes your facts do not support.", {
        violations: [{ code: "UNSUPPORTED_SKILL", message: "m", location: "paragraphs[1]", excerpt: "Kubernetes" }],
      })
    );
    const user = userEvent.setup();
    await user.click(letter().getByRole("button", { name: "Humanize cover letter" }));
    await user.click(screen.getByRole("button", { name: "Humanize" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("contained changes your facts do not support");
    expect(within(alert).getByLabelText("Why it was rejected")).toHaveTextContent("Kubernetes");
    // still version 1, still current, nothing viewable changed
    expect(currentStatus(LETTER_TITLE)).toHaveTextContent("Version 1");
    expect(letter().getByRole("button", { name: "Humanize cover letter" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: /version 2/ })).not.toBeInTheDocument();
  });

  it("shows other failures as errors, and can be tried again", async () => {
    await renderDocuments(makeList([letterSummary(1)]));
    humanizeMock.mockRejectedValue(new ApiError(429, "AI_RATE_LIMITED", "Too many requests.", {}));
    const user = userEvent.setup();
    await user.click(letter().getByRole("button", { name: "Humanize cover letter" }));
    await user.click(screen.getByRole("button", { name: "Humanize" }));
    expect(await screen.findByText("Too many requests.")).toBeInTheDocument();
    expect(letter().getByRole("button", { name: "Humanize cover letter" })).toBeEnabled();
  });

  it("tells the user their own edits will be superseded, never silently", async () => {
    await renderDocuments(makeList([letterSummary(2, { origin: "user_edited" })]));
    humanizeMock.mockResolvedValue(makeDocument({ version_number: 3, origin: "humanized" }, "cover_letter"));
    const user = userEvent.setup();
    await user.click(letter().getByRole("button", { name: "Humanize cover letter" }));
    expect(screen.getByRole("group", { name: "Confirm humanize" })).toHaveTextContent("contains your own edits");
    await user.click(screen.getByRole("button", { name: "Humanize" }));
    expect(humanizeMock).toHaveBeenCalledWith("app-1", { acknowledgeEditedCurrent: true });
  });
});

describe("regenerating over the user's edits", () => {
  it("asks first and then keeps the edited version in the history", async () => {
    await renderDocuments(makeList([cvSummary(2, { origin: "user_edited" })]));
    overEditsMock.mockResolvedValue(makeDocument({ version_number: 3 }));
    const user = userEvent.setup();

    await user.click(cv().getByRole("button", { name: "Regenerate Tailored CV" }));
    expect(generateDocumentMock).not.toHaveBeenCalled();
    expect(overEditsMock).not.toHaveBeenCalled();
    const confirm = screen.getByRole("group", { name: "Confirm regenerate" });
    expect(confirm).toHaveTextContent("contains your own edits");
    expect(confirm).toHaveTextContent("stay in the history");

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(overEditsMock).not.toHaveBeenCalled();

    await user.click(cv().getByRole("button", { name: "Regenerate Tailored CV" }));
    await user.click(screen.getByRole("button", { name: "Generate new version" }));
    expect(overEditsMock).toHaveBeenCalledWith("app-1", "tailored_cv");
    expect(generateDocumentMock).not.toHaveBeenCalled();
  });

  it("regenerates an AI version without any confirmation, as before", async () => {
    await renderDocuments(makeList([cvSummary(1)]));
    generateDocumentMock.mockResolvedValue(makeDocument({ version_number: 2 }));
    const user = userEvent.setup();
    await user.click(cv().getByRole("button", { name: "Regenerate Tailored CV" }));
    expect(generateDocumentMock).toHaveBeenCalledWith("app-1", "tailored_cv");
    expect(screen.queryByRole("group", { name: "Confirm regenerate" })).not.toBeInTheDocument();
  });

  it("marks the current edited version as the user's own words", async () => {
    await renderDocuments(makeList([cvSummary(2, { origin: "user_edited", validation_status: "user_authored" })]));
    expect(cv().getByText(/Edited by you — your own words, not checked by JobPilot/)).toBeInTheDocument();
    expect(cv().queryByText(/Checked against your resume/)).not.toBeInTheDocument();
  });
});

describe("editing, copying and downloading", () => {
  async function viewLetter(extra: Partial<GeneratedDocument> = {}) {
    const summary = letterSummary(1);
    await renderDocuments(makeList([summary]));
    getDocumentMock.mockResolvedValue(makeDocument({ id: summary.id, version_number: 1, ...extra }, "cover_letter"));
    const user = userEvent.setup();
    await user.click(letter().getByRole("button", { name: `View ${LETTER_TITLE}` }));
    await screen.findByRole("region", { name: `${LETTER_TITLE}, version 1` });
    return user;
  }

  it("offers Edit, Copy and Download beside a viewed document", async () => {
    await viewLetter();
    for (const name of [`Edit ${LETTER_TITLE}`, `Copy ${LETTER_TITLE}`, `Download ${LETTER_TITLE}`]) {
      expect(letter().getByRole("button", { name })).toBeEnabled();
    }
  });

  it("copies the whole letter as plain text", async () => {
    const user = await viewLetter();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(letter().getByRole("button", { name: `Copy ${LETTER_TITLE}` }));
    const copied = writeText.mock.calls[0]![0] as string;
    expect(copied).toContain("Bewerbung als Senior Backend Engineer (Python)");
    expect(copied).toContain("Sehr geehrte Damen und Herren,");
    expect(copied).toContain("In meiner Tätigkeit habe ich SAP IS-U Anwendungen entwickelt.");
    expect(copied).toContain("Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr.");
    expect(copied).toContain("Mit freundlichen Grüßen\n\nAlex Example");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("saves an edit as a new version; the version being edited is not replaced", async () => {
    const user = await viewLetter();
    const edited = makeDocument(
      { id: "letter-v2", version_number: 2, origin: "user_edited", validation_status: "user_authored" },
      "cover_letter"
    );
    editMock.mockResolvedValue({
      ...edited,
      content: makeLetterContent({ subject: "My own subject" }),
    });
    listDocumentsMock.mockResolvedValue(
      makeList([
        letterSummary(2, { id: "letter-v2", origin: "user_edited" }),
        { ...letterSummary(1), is_current: false },
      ])
    );

    await user.click(letter().getByRole("button", { name: `Edit ${LETTER_TITLE}` }));
    const editor = within(screen.getByRole("region", { name: `Edit ${LETTER_TITLE}` }));
    expect(editor.getByText(/this version stays exactly as it is/)).toBeInTheDocument();
    expect(editor.getByText(/JobPilot does not check it/)).toBeInTheDocument();
    const subject = editor.getByLabelText("Subject");
    await user.clear(subject);
    await user.type(subject, "My own subject");
    await user.click(editor.getByRole("button", { name: "Save as new version" }));

    expect(editMock).toHaveBeenCalledTimes(1);
    const [applicationId, documentId, request] = editMock.mock.calls[0]!;
    expect(applicationId).toBe("app-1");
    expect(documentId).toBe("letter-v1");
    expect(request).toMatchObject({
      kind: "cover_letter",
      subject: "My own subject",
      salutation: "Sehr geehrte Damen und Herren,",
      salary_statement: "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr.",
      closing_formula: "Mit freundlichen Grüßen",
      signature: "Alex Example",
    });
    expect(request.paragraphs).toHaveLength(3);

    const viewer = await screen.findByRole("region", { name: `${LETTER_TITLE}, version 2` });
    expect(viewer).toHaveTextContent("Your own words — not checked by JobPilot");
    expect(viewer).toHaveTextContent("My own subject");
    expect(screen.queryByRole("region", { name: `Edit ${LETTER_TITLE}` })).not.toBeInTheDocument();
  });

  it("shows the server's reason when nothing changed, and keeps the editor open", async () => {
    const user = await viewLetter();
    editMock.mockRejectedValue(new ApiError(409, "NO_CHANGES", "Nothing was changed, so there is nothing to save.", {}));
    await user.click(letter().getByRole("button", { name: `Edit ${LETTER_TITLE}` }));
    await user.click(within(screen.getByRole("region", { name: `Edit ${LETTER_TITLE}` })).getByRole("button", { name: "Save as new version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was changed");
    expect(screen.getByRole("region", { name: `Edit ${LETTER_TITLE}` })).toBeInTheDocument();
  });

  it("cancels an edit without saving", async () => {
    const user = await viewLetter();
    await user.click(letter().getByRole("button", { name: `Edit ${LETTER_TITLE}` }));
    await user.click(within(screen.getByRole("region", { name: `Edit ${LETTER_TITLE}` })).getByRole("button", { name: "Cancel" }));
    expect(editMock).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: `${LETTER_TITLE}, version 1` })).toBeInTheDocument();
  });

  it("edits a CV: summary, header and bullets only", async () => {
    const summary = cvSummary(1);
    await renderDocuments(makeList([summary]));
    getDocumentMock.mockResolvedValue(makeDocument({ id: summary.id, version_number: 1 }));
    editMock.mockResolvedValue(makeDocument({ id: "cv-v2", version_number: 2, origin: "user_edited" }));
    const user = userEvent.setup();
    await user.click(cv().getByRole("button", { name: "View Tailored CV" }));
    await screen.findByRole("region", { name: "Tailored CV, version 1" });
    await user.click(cv().getByRole("button", { name: "Edit Tailored CV" }));

    const editor = within(screen.getByRole("region", { name: "Edit Tailored CV" }));
    await user.clear(editor.getByLabelText("Summary"));
    await user.type(editor.getByLabelText("Summary"), "A summary I wrote.");
    await user.click(editor.getByRole("button", { name: "Add bullet to SAP IS-U Developer" }));
    await user.type(editor.getByLabelText("SAP IS-U Developer bullet 2"), "A bullet of my own.");
    await user.click(editor.getByRole("button", { name: "Save as new version" }));

    const request = editMock.mock.calls[0]![2];
    expect(request.kind).toBe("tailored_cv");
    expect(request.summary).toBe("A summary I wrote.");
    expect(request.header.full_name).toBe("Alex Example");
    expect(request.experience).toEqual([
      {
        source_fact_id: "exp1",
        bullets: ["Entwicklung und Wartung von SAP IS-U-Anwendungen", "A bullet of my own."],
      },
    ]);
    // employers, titles and dates are not editable here
    expect(editor.queryByLabelText(/company/i)).not.toBeInTheDocument();
  });

  it("marks the user's own words in a viewed edited document", async () => {
    const summary = cvSummary(2, { origin: "user_edited" });
    await renderDocuments(makeList([summary]));
    const content = makeCvContent({
      summary: { text: "Mine.", source_fact_ids: [], origin: "user" },
    });
    getDocumentMock.mockResolvedValue({
      ...makeDocument({ id: summary.id, version_number: 2, origin: "user_edited" }),
      content,
    });
    const user = userEvent.setup();
    await user.click(cv().getByRole("button", { name: "View Tailored CV" }));
    const viewer = await screen.findByRole("region", { name: "Tailored CV, version 2" });
    expect(within(viewer).getByText("(your wording)")).toBeInTheDocument();
  });
});

describe("the CV photo", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:photo"), configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  });

  const photo = { content_type: "image/png", file_name: "me.png", size_bytes: 10, updated_at: "2026-10-01T10:00:00Z" };

  it("offers to add a photo and says it is the user's own", async () => {
    await renderDocuments(makeList([cvSummary(1)]));
    const group = within(screen.getByRole("group", { name: "CV photo" }));
    expect(group.getByText("No photo")).toBeInTheDocument();
    expect(group.getByText(/never creates, changes or replaces it/)).toBeInTheDocument();
    expect(group.getByRole("button", { name: "Add photo" })).toBeEnabled();
    expect(group.queryByRole("button", { name: "Remove photo" })).not.toBeInTheDocument();
    expect(loadPhotoMock).not.toHaveBeenCalled();
  });

  it("uploads the chosen file and reloads", async () => {
    await renderDocuments(makeList([cvSummary(1)]));
    uploadPhotoMock.mockResolvedValue(photo);
    loadPhotoMock.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    listDocumentsMock.mockResolvedValue({ ...makeList([cvSummary(1)]), photo });
    const user = userEvent.setup();
    const file = new File(["png"], "me.png", { type: "image/png" });

    await user.upload(screen.getByTestId("photo-file"), file);

    expect(uploadPhotoMock).toHaveBeenCalledWith("app-1", file);
    const group = within(screen.getByRole("group", { name: "CV photo" }));
    expect(await group.findByRole("img", { name: "Your CV photo" })).toHaveAttribute("src", "blob:photo");
    expect(group.getByRole("button", { name: "Replace photo" })).toBeInTheDocument();
    expect(group.getByRole("button", { name: "Remove photo" })).toBeInTheDocument();
  });

  it("shows the photo on the CV, never inside the generated content", async () => {
    const summary = cvSummary(1);
    loadPhotoMock.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    await renderDocuments({ ...makeList([summary]), photo });
    getDocumentMock.mockResolvedValue(makeDocument({ id: summary.id, version_number: 1 }));
    const user = userEvent.setup();
    await user.click(cv().getByRole("button", { name: "View Tailored CV" }));
    const viewer = within(await screen.findByRole("region", { name: "Tailored CV, version 1" }));
    expect(await viewer.findByRole("img", { name: "Your photo" })).toHaveAttribute("src", "blob:photo");
  });

  it("removes the photo", async () => {
    loadPhotoMock.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    await renderDocuments({ ...makeList([cvSummary(1)]), photo });
    removePhotoMock.mockResolvedValue(undefined);
    listDocumentsMock.mockResolvedValue({ ...makeList([cvSummary(1)]), photo: null });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Remove photo" }));
    expect(removePhotoMock).toHaveBeenCalledWith("app-1");
    await waitFor(() => expect(screen.getByText("No photo")).toBeInTheDocument());
    expect(screen.queryByRole("img", { name: "Your CV photo" })).not.toBeInTheDocument();
  });

  it("shows why a photo was refused", async () => {
    await renderDocuments(makeList([cvSummary(1)]));
    uploadPhotoMock.mockRejectedValue(new ApiError(422, "VALIDATION_ERROR", "The photo must be a JPEG or PNG image.", {}));
    const user = userEvent.setup();
    await user.upload(screen.getByTestId("photo-file"), new File(["x"], "me.gif", { type: "image/png" }));
    expect(await screen.findByText("The photo must be a JPEG or PNG image.")).toBeInTheDocument();
  });
});
