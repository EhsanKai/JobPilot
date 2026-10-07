import { describe, expect, it } from "vitest";

import { cvToText, documentToText, letterToText } from "@/lib/documentText";
import {
  copyText,
  finalText,
  problemFromError,
  remainingCharacters,
  suggestionText,
  valueText,
} from "@/lib/questions";
import { ApiError } from "@/lib/http";

import { makeCvContent, makeLetterContent } from "./fixtures/documents";
import { makeAnswer, makeQuestion } from "./fixtures/questions";

describe("document text for copying", () => {
  it("renders a letter in reading order, with the salary sentence and the signature", () => {
    const text = letterToText(makeLetterContent());
    const order = [
      "Alex Example",
      "Acme GmbH",
      "Bewerbung als Senior Backend Engineer (Python)",
      "Sehr geehrte Damen und Herren,",
      "mit großem Interesse",
      "In meiner Tätigkeit",
      "Meine Gehaltsvorstellung liegt bei 78.000 EUR",
      "Mit freundlichen Grüßen\n\nAlex Example",
    ].map((part) => text.indexOf(part));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("leaves out a salary sentence the user does not want", () => {
    expect(letterToText(makeLetterContent({ salary_statement: null }))).not.toContain("Gehaltsvorstellung");
  });

  it("renders a CV with its sections", () => {
    const text = cvToText(makeCvContent());
    expect(text).toContain("Alex Example");
    expect(text).toContain("SUMMARY");
    expect(text).toContain("SAP IS-U Developer — Example Energy GmbH");
    expect(text).toContain("- Entwicklung und Wartung von SAP IS-U-Anwendungen");
    expect(text).toContain("SKILLS\nSAP ABAP, Git");
    expect(text).toContain("M.Sc., Wirtschaftsinformatik, Technische Universität Example (2016 – 2019)");
    expect(documentToText(makeCvContent())).toBe(text);
  });
});

describe("question helpers", () => {
  it("renders values as text or as joined options", () => {
    expect(valueText("x", null)).toBe("x");
    expect(valueText(null, ["A", "B"])).toBe("A, B");
    expect(valueText(null, null)).toBe("");
    const answer = makeAnswer({ ai_suggestion: "s", final_selected_options: ["Senior"] });
    expect(suggestionText(answer)).toBe("s");
    expect(finalText(answer)).toBe("Senior");
  });

  it("computes the remaining characters", () => {
    expect(remainingCharacters(makeQuestion({ max_length: 10 }), "1234")).toBe(6);
    expect(remainingCharacters(makeQuestion({ max_length: 3 }), "1234")).toBe(-1);
    expect(remainingCharacters(makeQuestion({ max_length: null }), "1234")).toBeNull();
  });

  it("classifies a failed request", () => {
    expect(problemFromError(new ApiError(502, "ANSWER_REJECTED", "m", { violations: [{ code: "X" }] })).kind).toBe("rejected");
    expect(problemFromError(new ApiError(422, "ANSWER_TOO_LONG", "m", {})).kind).toBe("invalid");
    expect(problemFromError(new ApiError(409, "RESUME_MISSING", "m", {})).kind).toBe("blocked");
    expect(problemFromError(new ApiError(503, "AI_NOT_CONFIGURED", "m", {})).kind).toBe("failed");
    expect(problemFromError(new Error("x")).kind).toBe("failed");
  });

  it("copies with the clipboard and reports a refusal", async () => {
    const original = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: () => Promise.reject(new Error("denied")) },
      configurable: true,
    });
    document.execCommand = () => false;
    expect(await copyText("x")).toBe(false);
    Object.defineProperty(navigator, "clipboard", { value: original, configurable: true });
  });
});
