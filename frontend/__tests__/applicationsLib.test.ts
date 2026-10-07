import { describe, expect, it } from "vitest";

import {
  buildPatch,
  fieldErrorsFromApi,
  firstInvalidField,
  formFromApplication,
  hasChanges,
  trimDecimal,
  validateForm,
  type ApplicationForm,
} from "@/lib/applications";
import { ApiError } from "@/lib/http";

import { makeApplication, makePreparedApplication } from "./fixtures/application";

function formFor(overrides: Partial<ApplicationForm> = {}, base = makeApplication()) {
  return { ...formFromApplication(base), ...overrides };
}

describe("formFromApplication", () => {
  it("starts empty for a fresh application: nothing is invented", () => {
    expect(formFromApplication(makeApplication())).toEqual({
      salary: "",
      currency: "",
      mentionSalary: "",
      availableFrom: "",
      noticeValue: "",
      noticeUnit: "months",
      workAuthorization: "",
      language: "",
      notes: "",
      resumeId: "resume-2",
    });
  });

  it("reflects every saved value", () => {
    expect(formFromApplication(makePreparedApplication())).toMatchObject({
      salary: "78000",
      currency: "EUR",
      mentionSalary: "yes",
      availableFrom: "2027-01-01",
      noticeValue: "3",
      noticeUnit: "months",
      workAuthorization: "authorized",
      language: "de",
      notes: "Referral from Alex.",
    });
  });

  it("tells 'decided not to mention' apart from 'not decided'", () => {
    expect(formFromApplication(makeApplication({ mention_salary_in_letter: false })).mentionSalary).toBe(
      "no"
    );
    expect(formFromApplication(makeApplication({ mention_salary_in_letter: null })).mentionSalary).toBe(
      ""
    );
  });

  it("has no resume selected when the application has none", () => {
    expect(formFromApplication(makeApplication({ resume: null })).resumeId).toBe("");
  });
});

describe("trimDecimal", () => {
  it.each([
    ["78000.00", "78000"],
    ["78000.50", "78000.5"],
    ["0.10", "0.1"],
    ["", ""],
    [null, ""],
  ])("%s -> %s", (input, expected) => {
    expect(trimDecimal(input)).toBe(expected);
  });
});

describe("validateForm", () => {
  it("accepts an empty form and a complete one", () => {
    expect(validateForm(formFor())).toEqual({});
    expect(validateForm(formFromApplication(makePreparedApplication()))).toEqual({});
  });

  it.each([
    ["0", "greater than 0"],
    ["-5", "greater than 0"],
    ["abc", "greater than 0"],
    ["1.234", "2 decimal places"],
    ["10000001", "too large"],
  ])("rejects a salary of %s", (salary, message) => {
    const errors = validateForm(formFor({ salary, currency: "EUR" }));
    expect(errors.salary).toContain(message);
  });

  it("needs a currency with a salary", () => {
    expect(validateForm(formFor({ salary: "78000" })).currency).toMatch(/currency/i);
    expect(validateForm(formFor({ salary: "78000", currency: "EUR" }))).toEqual({});
    // A currency alone is harmless.
    expect(validateForm(formFor({ currency: "EUR" }))).toEqual({});
  });

  it.each(["EU", "EURO", "E1R", "€€€"])("rejects the currency %s", (currency) => {
    expect(validateForm(formFor({ currency })).currency).toMatch(/3-letter/);
  });

  it.each(["1.5", "-1", "abc", "1e2"])("rejects the notice period %s", (noticeValue) => {
    expect(validateForm(formFor({ noticeValue })).noticeValue).toMatch(/whole number/);
  });

  it("accepts a notice period of 0", () => {
    expect(validateForm(formFor({ noticeValue: "0" }))).toEqual({});
  });
});

describe("buildPatch", () => {
  it("is empty when nothing changed", () => {
    const application = makePreparedApplication();
    expect(buildPatch(application, formFromApplication(application))).toEqual({});
    expect(hasChanges({})).toBe(false);
  });

  it("sends only the fields that changed", () => {
    const application = makePreparedApplication();
    const patch = buildPatch(application, formFor({ notes: "New note" }, application));
    expect(patch).toEqual({ notes: "New note" });
    expect(hasChanges(patch)).toBe(true);
  });

  it("sends the preparation fields in API shape", () => {
    const patch = buildPatch(
      makeApplication(),
      formFor({
        salary: "78000",
        currency: "eur",
        mentionSalary: "no",
        availableFrom: "2027-01-01",
        noticeValue: "3",
        noticeUnit: "weeks",
        workAuthorization: "requires_sponsorship",
        language: "en",
        notes: "  hello  ",
      })
    );
    expect(patch).toEqual({
      salary_expectation: "78000",
      salary_currency: "EUR",
      mention_salary_in_letter: false,
      available_from: "2027-01-01",
      notice_period: { value: 3, unit: "weeks" },
      work_authorization: "requires_sponsorship",
      application_language: "en",
      notes: "  hello  ",
    });
  });

  it("clears a field with null when the user empties it", () => {
    const application = makePreparedApplication();
    const patch = buildPatch(
      application,
      formFor(
        {
          salary: "",
          currency: "",
          mentionSalary: "",
          availableFrom: "",
          noticeValue: "",
          workAuthorization: "",
          language: "",
          notes: "",
        },
        application
      )
    );
    expect(patch).toEqual({
      salary_expectation: null,
      salary_currency: null,
      mention_salary_in_letter: null,
      available_from: null,
      notice_period: null,
      work_authorization: null,
      application_language: null,
      notes: null,
    });
  });

  it("sends the notice period again when only its unit changed", () => {
    const application = makePreparedApplication();
    expect(buildPatch(application, formFor({ noticeUnit: "weeks" }, application))).toEqual({
      notice_period: { value: 3, unit: "weeks" },
    });
  });

  it("ignores a unit change while there is no notice period", () => {
    expect(buildPatch(makeApplication(), formFor({ noticeUnit: "days" }))).toEqual({});
  });

  it("treats 'decided not to mention' as a change from 'not decided'", () => {
    expect(buildPatch(makeApplication(), formFor({ mentionSalary: "no" }))).toEqual({
      mention_salary_in_letter: false,
    });
  });

  it("sends a different resume, never an empty one", () => {
    const application = makeApplication();
    expect(buildPatch(application, formFor({ resumeId: "resume-1" }, application))).toEqual({
      resume_id: "resume-1",
    });
    expect(buildPatch(application, formFor({ resumeId: "" }, application))).toEqual({});
  });

  it("doesn't count whitespace-only differences in notes", () => {
    const application = makePreparedApplication({ notes: "Referral" });
    expect(buildPatch(application, formFor({ notes: "Referral  \n" }, application))).toEqual({});
    expect(
      buildPatch(makeApplication({ notes: null }), formFor({ notes: "   " }))
    ).toEqual({});
  });
});

describe("fieldErrorsFromApi", () => {
  it("maps request validation errors to the form fields", () => {
    const error = new ApiError(422, "VALIDATION_ERROR", "Salary expectation must be greater than 0.", {
      errors: [
        { loc: ["body", "salary_expectation"], msg: "Value error, Salary expectation must be greater than 0." },
        { loc: ["body", "notice_period", "value"], msg: "Input should be greater than or equal to 0" },
      ],
    });
    const { fields, general } = fieldErrorsFromApi(error);
    expect(fields.salary).toBe("Salary expectation must be greater than 0.");
    expect(fields.noticeValue).toBe("Input should be greater than or equal to 0");
    expect(general).toBe("Salary expectation must be greater than 0.");
  });

  it("maps service errors that name a field", () => {
    const error = new ApiError(422, "VALIDATION_ERROR", "Enter the currency.", {
      field: "salary_currency",
    });
    expect(fieldErrorsFromApi(error).fields).toEqual({ currency: "Enter the currency." });
  });

  it("maps a resume the user can't use", () => {
    const error = new ApiError(422, "VALIDATION_ERROR", "Resume not found.", { field: "resume_id" });
    expect(fieldErrorsFromApi(error).fields).toEqual({ resumeId: "Resume not found." });
  });

  it("leaves errors it can't place as general", () => {
    const error = new ApiError(500, "INTERNAL_ERROR", "An unexpected error occurred.", {});
    expect(fieldErrorsFromApi(error)).toEqual({
      fields: {},
      general: "An unexpected error occurred.",
    });
    const unknown = new ApiError(422, "VALIDATION_ERROR", "Extra inputs are not permitted", {
      errors: [{ loc: ["body", "status"], msg: "Extra inputs are not permitted" }],
    });
    expect(fieldErrorsFromApi(unknown).fields).toEqual({});
  });
});

describe("firstInvalidField", () => {
  it("finds nothing when there are no errors", () => {
    expect(firstInvalidField({})).toBeNull();
    expect(firstInvalidField({ salary: undefined })).toBeNull();
  });

  it("returns the first field in the order the page shows them", () => {
    expect(firstInvalidField({ notes: "x", currency: "y" })).toBe("currency");
    expect(firstInvalidField({ resumeId: "x", noticeValue: "y" })).toBe("noticeValue");
    expect(firstInvalidField({ language: "x", availableFrom: "y" })).toBe("availableFrom");
  });
});
