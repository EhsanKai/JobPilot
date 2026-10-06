import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationDocuments } from "@/components/applications/ApplicationDocuments";
import { ApiError } from "@/lib/http";
import type { DocumentSummary } from "@/types/documents";

import {
  READY,
  blocked,
  makeDocument,
  makeLetterContent,
  makeList,
  makeSummary,
} from "./fixtures/documents";

const listDocumentsMock = vi.fn();
const getDocumentMock = vi.fn();
const generateDocumentMock = vi.fn();

vi.mock("@/lib/api/documents", () => ({
  listDocuments: (...args: unknown[]) => listDocumentsMock(...args),
  getDocument: (...args: unknown[]) => getDocumentMock(...args),
  generateDocument: (...args: unknown[]) => generateDocumentMock(...args),
}));

interface Options {
  language?: string | null;
  hasUnsavedChanges?: boolean;
  reloadKey?: string;
}

async function renderDocuments(list = makeList(), options: Options = {}) {
  listDocumentsMock.mockResolvedValue(list);
  const view = render(
    <ApplicationDocuments
      applicationId="app-1"
      language={options.language === undefined ? "de" : options.language}
      reloadKey={options.reloadKey ?? "t0"}
      hasUnsavedChanges={options.hasUnsavedChanges ?? false}
      pollIntervalMs={10}
    />
  );
  await screen.findByRole("heading", { name: "Tailored CV" });
  return view;
}

function panel(name: string) {
  return within(screen.getByRole("heading", { name }).closest("section")!);
}

/** The "✓ Generated · Version N" status of a panel (the history list repeats versions). */
function currentStatus(name: string) {
  return screen
    .getByRole("heading", { name })
    .closest("section")!
    .querySelector('[data-status="generated"]');
}

const cv = () => panel("Tailored CV");
const letter = () => panel("Cover letter (Anschreiben)");

function validated(
  version: number,
  type: DocumentSummary["document_type"] = "tailored_cv",
  overrides: Partial<DocumentSummary> = {}
) {
  return makeSummary({
    document_type: type,
    version_number: version,
    is_current: true,
    prompt_version: type === "tailored_cv" ? "tailored_cv_v1" : "cover_letter_v1",
    ...overrides,
  });
}

describe("ApplicationDocuments", () => {
  beforeEach(() => {
    listDocumentsMock.mockReset();
    getDocumentMock.mockReset();
    generateDocumentMock.mockReset();
  });

  describe("the Documents section", () => {
    it("has a Documents heading and a panel for the CV and for the Anschreiben", async () => {
      await renderDocuments();

      expect(screen.getByRole("heading", { level: 2, name: "Documents" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Tailored CV" })).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "Cover letter (Anschreiben)" })
      ).toBeInTheDocument();
      expect(listDocumentsMock).toHaveBeenCalledWith("app-1");
    });

    it("shows a loading state first", async () => {
      listDocumentsMock.mockReturnValue(new Promise(() => {}));
      render(
        <ApplicationDocuments
          applicationId="app-1"
          language="de"
          reloadKey="t0"
          hasUnsavedChanges={false}
        />
      );
      expect(screen.getByText("Loading documents...")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Generate/ })).not.toBeInTheDocument();
    });

    it("says nothing was generated yet, and offers to generate each", async () => {
      await renderDocuments();

      expect(cv().getByText(/Status: Not generated/)).toBeInTheDocument();
      expect(letter().getByText(/Status: Not generated/)).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeEnabled();
      expect(letter().getByRole("button", { name: "Generate Anschreiben" })).toBeEnabled();
      expect(cv().queryByRole("button", { name: /View/ })).not.toBeInTheDocument();
      expect(cv().queryByRole("button", { name: /Regenerate/ })).not.toBeInTheDocument();
    });

    it("explains that every statement is checked against the resume", async () => {
      await renderDocuments();
      expect(screen.getByText(/checked\s+against your resume/)).toBeInTheDocument();
      expect(screen.getByText(/nothing is invented/)).toBeInTheDocument();
    });

    it("shows a load failure without hiding the rest of the workspace", async () => {
      listDocumentsMock.mockRejectedValue(new ApiError(500, "INTERNAL_ERROR", "boom", {}));
      render(
        <ApplicationDocuments
          applicationId="app-1"
          language="de"
          reloadKey="t0"
          hasUnsavedChanges={false}
        />
      );
      expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load your documents");
      expect(screen.getByRole("heading", { level: 2, name: "Documents" })).toBeInTheDocument();
    });
  });

  describe("language", () => {
    it.each([
      ["de", "German"],
      ["en", "English"],
    ])("says the documents are written in the application language (%s)", async (code, name) => {
      await renderDocuments(makeList(), { language: code });
      expect(screen.getByText(new RegExp(`written in ${name}`))).toBeInTheDocument();
    });

    it("doesn't claim a language when none is chosen, and says what to do", async () => {
      await renderDocuments(
        makeList([], {
          tailored_cv: blocked(["LANGUAGE_MISSING", "Select the application language first."]),
          cover_letter: blocked(["LANGUAGE_MISSING", "Select the application language first."]),
        }),
        { language: null }
      );
      expect(screen.queryByText(/written in/)).not.toBeInTheDocument();
      expect(cv().getByText("Select the application language first.")).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeDisabled();
    });

    it("shows the language a version was written in when viewing it", async () => {
      const summary = validated(1, "tailored_cv", { language: "en" });
      getDocumentMock.mockResolvedValue(makeDocument({ ...summary }));
      await renderDocuments(makeList([summary]));

      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV" }));
      const region = await screen.findByRole("region", { name: "Tailored CV, version 1" });
      expect(within(region).getByText("English")).toBeInTheDocument();
    });
  });

  describe("generating", () => {
    it("generates the CV, with a loading state, and then shows version 1", async () => {
      let finish!: () => void;
      generateDocumentMock.mockReturnValue(new Promise<void>((resolve) => (finish = resolve)));
      await renderDocuments();

      await userEvent.click(cv().getByRole("button", { name: "Generate CV" }));

      // loading: the button and the status say so, and nothing can be clicked twice
      expect(generateDocumentMock).toHaveBeenCalledTimes(1);
      expect(generateDocumentMock).toHaveBeenCalledWith("app-1", "tailored_cv");
      expect(cv().getByRole("status")).toHaveTextContent("Generating...");
      expect(cv().getByRole("button", { name: /Generating/ })).toBeDisabled();
      expect(letter().getByRole("button", { name: "Generate Anschreiben" })).toBeEnabled();

      listDocumentsMock.mockResolvedValue(makeList([validated(1)]));
      finish();

      expect(await cv().findByText("✓ Generated")).toBeInTheDocument();
      expect(currentStatus("Tailored CV")).toHaveTextContent(/Version 1/);
      expect(currentStatus("Tailored CV")).toHaveTextContent(/Generated today/);
      expect(cv().getByText(/Checked against your resume \(resume version 2\)/)).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "View Tailored CV" })).toBeEnabled();
      expect(cv().getByRole("button", { name: "Regenerate Tailored CV" })).toBeEnabled();
      expect(cv().queryByRole("button", { name: "Generate CV" })).not.toBeInTheDocument();
    });

    it("generates the Anschreiben on its own, without touching the CV", async () => {
      generateDocumentMock.mockResolvedValue(undefined);
      await renderDocuments();
      listDocumentsMock.mockResolvedValue(makeList([validated(1, "cover_letter")]));

      await userEvent.click(letter().getByRole("button", { name: "Generate Anschreiben" }));

      expect(generateDocumentMock).toHaveBeenCalledTimes(1);
      expect(generateDocumentMock).toHaveBeenCalledWith("app-1", "cover_letter");
      expect(await letter().findByText("✓ Generated")).toBeInTheDocument();
      expect(cv().getByText(/Status: Not generated/)).toBeInTheDocument();
    });

    it("regenerates the CV only: a new version, and the letter is left alone", async () => {
      const letterV1 = validated(1, "cover_letter");
      await renderDocuments(makeList([letterV1, validated(1)]));
      generateDocumentMock.mockResolvedValue(undefined);
      const cvV2 = validated(2);
      const cvV1 = validated(1, "tailored_cv", { is_current: false });
      listDocumentsMock.mockResolvedValue(makeList([cvV2, letterV1, cvV1]));

      await userEvent.click(cv().getByRole("button", { name: "Regenerate Tailored CV" }));

      expect(generateDocumentMock).toHaveBeenCalledTimes(1);
      expect(generateDocumentMock).toHaveBeenCalledWith("app-1", "tailored_cv");
      await waitFor(() => expect(currentStatus("Tailored CV")).toHaveTextContent(/Version 2/));
      expect(currentStatus("Cover letter (Anschreiben)")).toHaveTextContent(/Version 1/);
      // the old version is kept in the history
      await userEvent.click(cv().getByText(/History \(2 attempts\)/));
      expect(cv().getByRole("button", { name: "View Tailored CV version 1" })).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "View Tailored CV version 2" })).toBeInTheDocument();
    });

    it("regenerates the Anschreiben only", async () => {
      await renderDocuments(makeList([validated(1), validated(1, "cover_letter")]));
      generateDocumentMock.mockResolvedValue(undefined);
      listDocumentsMock.mockResolvedValue(
        makeList([
          validated(2, "cover_letter"),
          validated(1),
          validated(1, "cover_letter", { is_current: false }),
        ])
      );

      await userEvent.click(letter().getByRole("button", { name: "Regenerate Cover letter (Anschreiben)" }));

      expect(generateDocumentMock).toHaveBeenCalledWith("app-1", "cover_letter");
      await waitFor(() => expect(currentStatus("Cover letter (Anschreiben)")).toHaveTextContent(/Version 2/));
      expect(currentStatus("Tailored CV")).toHaveTextContent(/Version 1/);
    });

    it("shows documents that already exist after a reload", async () => {
      await renderDocuments(
        makeList([validated(3), validated(1, "cover_letter", { language: "en" })])
      );
      expect(cv().getByText("✓ Generated")).toBeInTheDocument();
      expect(currentStatus("Tailored CV")).toHaveTextContent(/Version 3/);
      expect(letter().getByText("✓ Generated")).toBeInTheDocument();
      expect(currentStatus("Cover letter (Anschreiben)")).toHaveTextContent(/Version 1/);
    });

    it("re-reads the documents when the application is saved", async () => {
      const { rerender } = await renderDocuments(makeList(), { reloadKey: "t0" });
      expect(listDocumentsMock).toHaveBeenCalledTimes(1);
      listDocumentsMock.mockResolvedValue(
        makeList([], { cover_letter: blocked(["SALARY_DECISION_REQUIRED", "Decide first."]) })
      );
      rerender(
        <ApplicationDocuments
          applicationId="app-1"
          language="de"
          reloadKey="t1"
          hasUnsavedChanges={false}
        />
      );
      expect(await letter().findByText("Decide first.")).toBeInTheDocument();
      expect(listDocumentsMock).toHaveBeenCalledTimes(2);
    });
  });

  describe("failures", () => {
    it("shows a failed generation as a failure, never as a document", async () => {
      generateDocumentMock.mockRejectedValue(
        new ApiError(504, "AI_TIMEOUT", "The AI service took too long to respond. Please try again.", {
          document_id: "doc-x",
        })
      );
      await renderDocuments();
      listDocumentsMock.mockResolvedValue(
        makeList([
          makeSummary({
            status: "failed",
            error_code: "AI_TIMEOUT",
            error_message: "The AI service took too long to respond. Please try again.",
          }),
        ])
      );

      await userEvent.click(cv().getByRole("button", { name: "Generate CV" }));

      expect(await cv().findByRole("alert")).toHaveTextContent("took too long to respond");
      expect(cv().queryByText("✓ Generated")).not.toBeInTheDocument();
      expect(cv().getByText(/Status: Not generated — the last attempt failed/)).toBeInTheDocument();
      expect(cv().queryByRole("button", { name: /View/ })).not.toBeInTheDocument();
      // and it can simply be tried again
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeEnabled();
    });

    it("shows a rejected document with what was wrong, and that nothing was saved", async () => {
      generateDocumentMock.mockRejectedValue(
        new ApiError(
          502,
          "DOCUMENT_REJECTED",
          "The generated document contained claims your resume does not support, so it was discarded and nothing was saved as a document. Please try again.",
          {
            document_id: "doc-x",
            violations: [
              {
                code: "UNSUPPORTED_TECHNOLOGY",
                message: "m",
                location: "experience[0].bullets[0]",
                excerpt: "Kubernetes",
              },
              { code: "UNSUPPORTED_NUMBER", message: "m", location: "summary", excerpt: "12" },
            ],
          }
        )
      );
      await renderDocuments();
      listDocumentsMock.mockResolvedValue(makeList([makeSummary({ status: "rejected" })]));

      await userEvent.click(cv().getByRole("button", { name: "Generate CV" }));

      const alert = await cv().findByRole("alert");
      expect(alert).toHaveTextContent("nothing was saved as a document");
      const reasons = within(alert).getByRole("list", { name: "Why it was rejected" });
      expect(reasons).toHaveTextContent("A tool or technology your resume doesn't state: “Kubernetes”");
      expect(reasons).toHaveTextContent("A number your resume doesn't state: “12”");
      expect(cv().queryByText("✓ Generated")).not.toBeInTheDocument();
      expect(cv().getByText(/the last attempt was rejected/)).toBeInTheDocument();
    });

    it("keeps the previous version current when a regeneration is rejected", async () => {
      const previous = validated(1);
      await renderDocuments(makeList([previous]));
      generateDocumentMock.mockRejectedValue(
        new ApiError(502, "DOCUMENT_REJECTED", "Discarded.", { violations: [] })
      );
      listDocumentsMock.mockResolvedValue(
        makeList([makeSummary({ status: "rejected" }), previous])
      );

      await userEvent.click(cv().getByRole("button", { name: "Regenerate Tailored CV" }));

      await waitFor(() =>
        expect(cv().getByText(/The latest attempt was rejected, so this version is still the current one/)).toBeInTheDocument()
      );
      expect(currentStatus("Tailored CV")).toHaveTextContent(/Version 1/);
      expect(cv().getByRole("button", { name: "View Tailored CV" })).toBeInTheDocument();
    });

    it("explains a rejected attempt from the history", async () => {
      const rejected = makeSummary({ status: "rejected", violation_count: 1 });
      getDocumentMock.mockResolvedValue({
        ...makeDocument({ ...rejected, content: null }),
        validation: {
          schema_version: "document_validation_v1",
          status: "failed",
          text_units_checked: 3,
          violations: [
            {
              code: "UNSUPPORTED_ORGANIZATION",
              message: "m",
              location: "summary",
              excerpt: "Initech GmbH",
            },
          ],
        },
      });
      await renderDocuments(makeList([rejected]));

      await userEvent.click(cv().getByText(/History \(1 attempt\)/));
      expect(cv().getByText(/Rejected · 1 unsupported claim · nothing saved/)).toBeInTheDocument();
      await userEvent.click(cv().getByRole("button", { name: "Show why attempt was rejected" }));

      expect(
        await cv().findByText("An employer or organization your resume doesn't state: “Initech GmbH”")
      ).toBeInTheDocument();
      expect(cv().queryByRole("button", { name: /^View/ })).not.toBeInTheDocument();
    });

    it("shows a blocked request's message calmly", async () => {
      generateDocumentMock.mockRejectedValue(
        new ApiError(409, "RESUME_NOT_CONFIRMED", "Confirm your resume first.", {})
      );
      await renderDocuments();

      await userEvent.click(cv().getByRole("button", { name: "Generate CV" }));

      expect(await cv().findByText("Confirm your resume first.")).toBeInTheDocument();
      expect(cv().queryByRole("alert")).not.toBeInTheDocument();
    });

    it("handles an unexpected error without exposing it", async () => {
      generateDocumentMock.mockRejectedValue(new Error("socket hang up: secret-token"));
      await renderDocuments();

      await userEvent.click(cv().getByRole("button", { name: "Generate CV" }));

      expect(await cv().findByRole("alert")).toHaveTextContent("Generation failed. Please try again.");
      expect(screen.queryByText(/secret-token/)).not.toBeInTheDocument();
    });
  });

  describe("what blocks generation", () => {
    it("tells the user a salary decision is needed before the Anschreiben, but not before the CV", async () => {
      await renderDocuments(
        makeList([], {
          cover_letter: blocked([
            "SALARY_DECISION_REQUIRED",
            "You set a salary expectation but haven't decided whether the letter should mention it.",
          ]),
        })
      );

      const blockers = letter().getByTestId("cover_letter-blockers");
      expect(blockers).toHaveTextContent("A salary decision is needed first.");
      expect(blockers).toHaveTextContent("haven't decided whether the letter should mention it");
      expect(within(blockers).getByRole("link", { name: "Go to Application preparation" })).toHaveAttribute(
        "href",
        "#preparation-heading"
      );
      expect(letter().getByRole("button", { name: "Generate Anschreiben" })).toBeDisabled();
      expect(cv().queryByTestId("tailored_cv-blockers")).not.toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeEnabled();
    });

    it("lists what is missing, e.g. an unconfirmed resume", async () => {
      const resume = blocked([
        "RESUME_NOT_CONFIRMED",
        "Review and confirm the analysis of resume version 2 first.",
      ]);
      await renderDocuments(makeList([], { tailored_cv: resume, cover_letter: resume }));

      expect(cv().getByTestId("tailored_cv-blockers")).toHaveTextContent("Before you can generate this:");
      expect(cv().getByText(/Review and confirm the analysis of resume version 2 first/)).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeDisabled();
      expect(letter().getByRole("button", { name: "Generate Anschreiben" })).toBeDisabled();
    });

    it("never generates from unsaved changes", async () => {
      await renderDocuments(makeList(), { hasUnsavedChanges: true });

      expect(screen.getByText(/You have unsaved changes/)).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeDisabled();
      expect(letter().getByRole("button", { name: "Generate Anschreiben" })).toBeDisabled();
      expect(generateDocumentMock).not.toHaveBeenCalled();
    });
  });

  describe("versions and freshness", () => {
    it("marks a document that is out of date and says why", async () => {
      await renderDocuments(makeList([validated(2, "tailored_cv", { is_outdated: true })]));
      expect(cv().getByText(/Outdated: your resume or application data changed/)).toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Regenerate Tailored CV" })).toBeEnabled();
    });

    it("doesn't call a current document outdated", async () => {
      await renderDocuments(makeList([validated(2)]));
      expect(cv().queryByText(/Outdated/)).not.toBeInTheDocument();
    });

    it("lists every attempt in the history, newest first, with its version", async () => {
      await renderDocuments(
        makeList([
          validated(3),
          makeSummary({ status: "rejected" }),
          makeSummary({
            status: "failed",
            error_message: "The AI service is temporarily unavailable. Please try again later.",
          }),
          validated(2, "tailored_cv", { is_current: false }),
          validated(1, "tailored_cv", { is_current: false }),
        ])
      );

      await userEvent.click(cv().getByText(/History \(5 attempts\)/));
      const items = cv().getAllByRole("listitem");
      expect(items.map((item) => item.getAttribute("data-status"))).toEqual([
        "validated",
        "rejected",
        "failed",
        "validated",
        "validated",
      ]);
      expect(items[0]).toHaveTextContent("Version 3 (current) · validated");
      expect(items[2]).toHaveTextContent("Failed · The AI service is temporarily unavailable");
      expect(items[4]).toHaveTextContent("Version 1 · validated");
    });
  });

  describe("viewing", () => {
    it("shows the generated CV with its content, provenance and validation", async () => {
      const summary = validated(2);
      getDocumentMock.mockResolvedValue(makeDocument({ ...summary }));
      await renderDocuments(makeList([summary]));

      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV" }));

      expect(getDocumentMock).toHaveBeenCalledWith("app-1", summary.id);
      const region = await screen.findByRole("region", { name: "Tailored CV, version 2" });
      const view = within(region);
      expect(view.getByText("Alex Example")).toBeInTheDocument();
      expect(view.getByText(/alex\.example@example\.com/)).toBeInTheDocument();
      expect(view.getByText("SAP-Entwickler mit Erfahrung in SAP IS-U und ABAP.")).toBeInTheDocument();
      expect(view.getByText("SAP IS-U Developer — Example Energy GmbH")).toBeInTheDocument();
      expect(view.getByText(/Berlin · 2023-01 – present/)).toBeInTheDocument();
      expect(view.getByText("Entwicklung und Wartung von SAP IS-U-Anwendungen")).toBeInTheDocument();
      expect(view.getByText(/M\.Sc\., Wirtschaftsinformatik, Technische Universität Example \(2016 – 2019\)/)).toBeInTheDocument();
      expect(view.getByText("German — native")).toBeInTheDocument();
      expect(view.getByText(/SAP Certified Development Associate, SAP, 2020/)).toBeInTheDocument();
      // validation and provenance
      expect(view.getByText(/Passed — every statement is backed by your resume/)).toBeInTheDocument();
      expect(view.getByText(/anthropic \(claude-sonnet-5-5\) · tailored_cv_v1/)).toBeInTheDocument();
      expect(view.getByText("From resume version")).toBeInTheDocument();
    });

    it("highlights the relevant skills without adding any", async () => {
      const summary = validated(1);
      getDocumentMock.mockResolvedValue(makeDocument({ ...summary }));
      await renderDocuments(makeList([summary]));

      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV" }));
      const skills = within(await screen.findByRole("region", { name: "Skills" }));

      expect(skills.getByText("SAP ABAP")).toHaveAttribute("data-emphasized", "true");
      expect(skills.getByText("Git")).toHaveAttribute("data-emphasized", "false");
      expect(skills.getByText(/Nothing was added to your skills/)).toBeInTheDocument();
      expect(skills.getAllByRole("listitem")).toHaveLength(2);
    });

    it("lets the reader trace a statement to the resume fact it comes from", async () => {
      const summary = validated(1);
      getDocumentMock.mockResolvedValue(makeDocument({ ...summary }));
      await renderDocuments(makeList([summary]));

      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV" }));
      const experience = within(await screen.findByRole("region", { name: "Experience" }));

      await userEvent.click(experience.getByText("Sources (1)"));
      expect(experience.getByText("Developed and maintained SAP IS-U applications")).toBeVisible();
    });

    it("shows the generated Anschreiben in full, including the salary sentence only when present", async () => {
      const summary = validated(1, "cover_letter");
      getDocumentMock.mockResolvedValue(makeDocument({ ...summary }, "cover_letter"));
      await renderDocuments(makeList([summary]));

      await userEvent.click(letter().getByRole("button", { name: "View Cover letter (Anschreiben)" }));
      const region = await screen.findByRole("region", {
        name: "Cover letter (Anschreiben), version 1",
      });
      const view = within(region);

      expect(view.getByText("Bewerbung als Senior Backend Engineer (Python)")).toBeInTheDocument();
      expect(view.getByText("Sehr geehrte Damen und Herren,")).toBeInTheDocument();
      expect(view.getByText("mit großem Interesse bewerbe ich mich auf die Position.")).toBeInTheDocument();
      expect(view.getByRole("group", { name: "Salary statement" })).toHaveTextContent(
        "Meine Gehaltsvorstellung liegt bei 78.000 EUR brutto pro Jahr."
      );
      expect(view.getByText("Mit freundlichen Grüßen")).toBeInTheDocument();
      expect(view.getByText("Acme GmbH")).toBeInTheDocument();
    });

    it("shows no salary sentence when the user didn't choose to mention it", async () => {
      const summary = validated(1, "cover_letter");
      getDocumentMock.mockResolvedValue({
        ...makeDocument({ ...summary }, "cover_letter"),
        content: makeLetterContent({ salary_statement: null }),
      });
      await renderDocuments(makeList([summary]));

      await userEvent.click(letter().getByRole("button", { name: "View Cover letter (Anschreiben)" }));
      await screen.findByRole("region", { name: "Cover letter (Anschreiben), version 1" });

      expect(screen.queryByText(/Gehaltsvorstellung/)).not.toBeInTheDocument();
    });

    it("opens an older version from the history and can hide it again", async () => {
      const v2 = validated(2);
      const v1 = validated(1, "tailored_cv", { is_current: false });
      getDocumentMock.mockImplementation(async (_app: string, id: string) =>
        makeDocument({ ...(id === v1.id ? v1 : v2) })
      );
      await renderDocuments(makeList([v2, v1]));

      await userEvent.click(cv().getByText(/History \(2 attempts\)/));
      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV version 1" }));

      expect(await screen.findByRole("region", { name: "Tailored CV, version 1" })).toBeInTheDocument();
      await userEvent.click(cv().getByRole("button", { name: "Hide Tailored CV" }));
      expect(screen.queryByRole("region", { name: /Tailored CV, version/ })).not.toBeInTheDocument();
    });

    it("says when a document can't be opened", async () => {
      getDocumentMock.mockRejectedValue(new ApiError(404, "NOT_FOUND", "Document not found.", {}));
      await renderDocuments(makeList([validated(1)]));

      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Unable to open this document");
    });

    it("closes the viewer when a new version is generated", async () => {
      const v1 = validated(1);
      getDocumentMock.mockResolvedValue(makeDocument({ ...v1 }));
      await renderDocuments(makeList([v1]));
      await userEvent.click(cv().getByRole("button", { name: "View Tailored CV" }));
      await screen.findByRole("region", { name: "Tailored CV, version 1" });

      generateDocumentMock.mockResolvedValue(undefined);
      listDocumentsMock.mockResolvedValue(
        makeList([validated(2), { ...v1, is_current: false }])
      );
      await userEvent.click(cv().getByRole("button", { name: "Regenerate Tailored CV" }));

      await waitFor(() => expect(currentStatus("Tailored CV")).toHaveTextContent(/Version 2/));
      expect(screen.queryByRole("region", { name: "Tailored CV, version 1" })).not.toBeInTheDocument();
    });
  });

  describe("a generation that is running on the server", () => {
    it("follows it until it ends, then shows the document", async () => {
      const running = makeSummary({ status: "generating" });
      await renderDocuments(makeList([running]));

      expect(cv().getByRole("status")).toHaveTextContent("started in another window");
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeDisabled();

      listDocumentsMock.mockResolvedValue(makeList([validated(1)]));
      expect(await cv().findByText("✓ Generated")).toBeInTheDocument();
      expect(cv().queryByRole("status")).not.toBeInTheDocument();
    });

    it("shows an abandoned generation as failed, not as running", async () => {
      await renderDocuments(
        makeList([
          makeSummary({
            status: "failed",
            error_code: "GENERATION_ABANDONED",
            error_message: "This generation did not finish. Please try again.",
          }),
        ])
      );

      expect(cv().queryByRole("status")).not.toBeInTheDocument();
      expect(cv().getByRole("button", { name: "Generate CV" })).toBeEnabled();
      expect(cv().getByText(/the last attempt failed/)).toBeInTheDocument();
    });
  });

  it("is ready by default in the fixtures", () => {
    expect(READY.can_generate).toBe(true);
  });
});
