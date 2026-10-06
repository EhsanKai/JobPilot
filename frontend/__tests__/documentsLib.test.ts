import { describe, expect, it } from "vitest";

import {
  attemptsOf,
  currentDocument,
  describeDates,
  describeGenerated,
  isGenerating,
  languageName,
  pendingAttempt,
  problemFromApi,
  violationLabel,
} from "@/lib/documents";
import { ApiError } from "@/lib/http";

import { makeSummary } from "./fixtures/documents";

describe("languageName", () => {
  it("names the languages applications can be written in", () => {
    expect(languageName("de")).toBe("German");
    expect(languageName("en")).toBe("English");
  });
  it("shows an unknown code in capitals and nothing for none", () => {
    expect(languageName("fr")).toBe("FR");
    expect(languageName(null)).toBeNull();
  });
});

describe("describeGenerated", () => {
  const now = new Date(2026, 9, 6, 15, 0, 0);
  it("says today and yesterday", () => {
    expect(describeGenerated(new Date(2026, 9, 6, 8, 0, 0).toISOString(), now)).toBe("today");
    expect(describeGenerated(new Date(2026, 9, 5, 23, 0, 0).toISOString(), now)).toBe("yesterday");
  });
  it("shows a date otherwise", () => {
    expect(describeGenerated(new Date(2026, 8, 1, 12, 0, 0).toISOString(), now)).toBe("1 Sept 2026");
  });
  it("shows nothing for a missing or invalid time", () => {
    expect(describeGenerated(null, now)).toBeNull();
    expect(describeGenerated("not a date", now)).toBeNull();
  });
});

describe("describeDates", () => {
  it("uses the resume's dates, 'present' for a current role", () => {
    expect(
      describeDates({ start_date: "2023-01", end_date: null, is_current: true, date_text: "x" })
    ).toBe("2023-01 – present");
    expect(
      describeDates({ start_date: "2019-03", end_date: "2022-12", is_current: false, date_text: null })
    ).toBe("2019-03 – 2022-12");
  });
  it("falls back to the text as written, and never invents an end", () => {
    expect(
      describeDates({ start_date: null, end_date: null, is_current: null, date_text: "since 2019" })
    ).toBe("since 2019");
    expect(
      describeDates({ start_date: "2019", end_date: null, is_current: false, date_text: null })
    ).toBe("2019");
  });
});

describe("attempts", () => {
  const v2 = makeSummary({ version_number: 2 });
  const rejected = makeSummary({ status: "rejected" });
  const v1 = makeSummary({ version_number: 1, is_current: false });
  const letter = makeSummary({ document_type: "cover_letter", version_number: 1 });

  it("finds the current document of a type", () => {
    expect(currentDocument([rejected, v2, v1, letter], "tailored_cv")).toBe(v2);
    expect(currentDocument([rejected, v2, v1, letter], "cover_letter")).toBe(letter);
    expect(currentDocument([rejected], "tailored_cv")).toBeNull();
  });

  it("lists the attempts of a type, in the order given", () => {
    expect(attemptsOf([rejected, v2, v1, letter], "tailored_cv")).toEqual([rejected, v2, v1]);
  });

  it("reports the newest attempt only when it did not produce a document", () => {
    expect(pendingAttempt([rejected, v2, v1], "tailored_cv")).toBe(rejected);
    expect(pendingAttempt([v2, rejected, v1], "tailored_cv")).toBeNull();
    expect(pendingAttempt([], "tailored_cv")).toBeNull();
  });

  it("knows when a generation is running", () => {
    const running = makeSummary({ status: "generating", document_type: "cover_letter" });
    expect(isGenerating([v2, running])).toBe(true);
    expect(isGenerating([v2, running], "cover_letter")).toBe(true);
    expect(isGenerating([v2, running], "tailored_cv")).toBe(false);
    expect(isGenerating([v2, rejected])).toBe(false);
  });
});

describe("problemFromApi", () => {
  it("keeps what was rejected", () => {
    const problem = problemFromApi(
      new ApiError(502, "DOCUMENT_REJECTED", "Discarded.", {
        violations: [{ code: "UNSUPPORTED_NUMBER", message: "m", location: "summary", excerpt: "12" }],
      })
    );
    expect(problem.kind).toBe("rejected");
    expect(problem.violations).toHaveLength(1);
  });
  it("treats a 409 as something to do, anything else as a failure", () => {
    expect(problemFromApi(new ApiError(409, "RESUME_MISSING", "Add a resume.", {})).kind).toBe("blocked");
    expect(problemFromApi(new ApiError(504, "AI_TIMEOUT", "Too slow.", {})).kind).toBe("failed");
  });
  it("copes with a rejection without details", () => {
    expect(problemFromApi(new ApiError(502, "DOCUMENT_REJECTED", "Discarded.", {})).violations).toEqual([]);
  });
});

describe("violationLabel", () => {
  it("explains each reason in plain language, and never shows a raw code", () => {
    const label = violationLabel({ code: "UNSUPPORTED_CREDENTIAL", message: "", location: "", excerpt: null });
    expect(label).toBe("A certification or degree your resume doesn't state");
    expect(
      violationLabel({ code: "SOMETHING_NEW", message: "", location: "", excerpt: null })
    ).toBe("Something your resume doesn't support");
  });
});
