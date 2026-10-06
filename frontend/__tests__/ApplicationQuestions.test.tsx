import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApplicationQuestions } from "@/components/applications/ApplicationQuestions";
import { ApiError } from "@/lib/http";
import type { Question } from "@/types/questions";

import { makeAnswer, makeQuestion, makeQuestionList, suggestedAnswer } from "./fixtures/questions";

const api = {
  listQuestions: vi.fn(),
  createQuestion: vi.fn(),
  updateQuestion: vi.fn(),
  deleteQuestion: vi.fn(),
  generateAnswer: vi.fn(),
  regenerateAnswer: vi.fn(),
  acceptSuggestion: vi.fn(),
  rejectSuggestion: vi.fn(),
  saveAnswer: vi.fn(),
  clearAnswer: vi.fn(),
};

vi.mock("@/lib/api/questions", () => ({
  listQuestions: (...a: unknown[]) => api.listQuestions(...a),
  createQuestion: (...a: unknown[]) => api.createQuestion(...a),
  updateQuestion: (...a: unknown[]) => api.updateQuestion(...a),
  deleteQuestion: (...a: unknown[]) => api.deleteQuestion(...a),
  generateAnswer: (...a: unknown[]) => api.generateAnswer(...a),
  regenerateAnswer: (...a: unknown[]) => api.regenerateAnswer(...a),
  acceptSuggestion: (...a: unknown[]) => api.acceptSuggestion(...a),
  rejectSuggestion: (...a: unknown[]) => api.rejectSuggestion(...a),
  saveAnswer: (...a: unknown[]) => api.saveAnswer(...a),
  clearAnswer: (...a: unknown[]) => api.clearAnswer(...a),
}));

async function renderQuestions(items: Question[] = [], ai = { can_generate: true, blockers: [] as { code: string; message: string }[] }) {
  api.listQuestions.mockResolvedValue(makeQuestionList(items, ai));
  render(<ApplicationQuestions applicationId="app-1" />);
  await screen.findByRole("heading", { name: "Application questions" });
  await waitFor(() => expect(screen.queryByText("Loading questions...")).not.toBeInTheDocument());
}

function card(text: string) {
  return within(screen.getByRole("article", { name: text }));
}

function yourAnswer(text: string) {
  return within(card(text).getByRole("region", { name: "Your answer" }));
}

const WHY = "Why do you want to work for our company?";

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
});

describe("the question list", () => {
  it("loads the questions of the application", async () => {
    await renderQuestions([makeQuestion({ question_text: WHY }), makeQuestion({ question_text: "Salary?" })]);
    expect(api.listQuestions).toHaveBeenCalledWith("app-1");
    expect(screen.getByRole("article", { name: WHY })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Salary?" })).toBeInTheDocument();
  });

  it("says there are no questions yet and offers to add one", async () => {
    await renderQuestions([]);
    expect(screen.getByText("No questions yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add question" })).toBeInTheDocument();
  });

  it("shows a loading state and a load error", async () => {
    api.listQuestions.mockReturnValue(new Promise(() => {}));
    const { unmount } = render(<ApplicationQuestions applicationId="app-1" />);
    expect(screen.getByText("Loading questions...")).toBeInTheDocument();
    unmount();

    api.listQuestions.mockRejectedValue(new Error("boom"));
    render(<ApplicationQuestions applicationId="app-1" />);
    expect(await screen.findByText("Unable to load your questions. Please try again.")).toBeInTheDocument();
  });

  it("shows the type, the limit and that an answer is required", async () => {
    await renderQuestions([
      makeQuestion({ question_text: WHY, required: true, max_length: 500 }),
    ]);
    expect(card(WHY).getByText("Required")).toBeInTheDocument();
    expect(card(WHY).getByText("Max 500 characters")).toBeInTheDocument();
    expect(card(WHY).getByText("Free text")).toBeInTheDocument();
  });

  it("explains when AI suggestions are not available without blocking manual answers", async () => {
    await renderQuestions([makeQuestion({ question_text: WHY })], {
      can_generate: false,
      blockers: [{ code: "RESUME_NOT_CONFIRMED", message: "Confirm your resume first." }],
    });
    expect(screen.getByTestId("ai-blockers")).toHaveTextContent("Confirm your resume first.");
    expect(screen.getByTestId("ai-blockers")).toHaveTextContent("answering by hand still work");
    expect(card(WHY).getByRole("button", { name: "Answer manually" })).toBeEnabled();
  });
});

describe("adding, editing and deleting a question", () => {
  it("creates a question with a type, a limit and options", async () => {
    await renderQuestions([]);
    const created = makeQuestion({
      question_text: "Seniority?",
      question_type: "SINGLE_CHOICE",
      options: ["Junior", "Senior"],
      required: true,
      max_length: 20,
    });
    api.createQuestion.mockResolvedValue(created);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Add question" }));
    await user.type(screen.getByLabelText("Question"), "Seniority?");
    await user.selectOptions(screen.getByLabelText("Answer type"), "SINGLE_CHOICE");
    await user.type(screen.getByLabelText(/Options/), "Junior{Enter}Senior");
    await user.type(screen.getByLabelText("Max characters"), "20");
    await user.click(screen.getByLabelText("Required"));
    await user.click(screen.getByRole("button", { name: "Add question" }));

    expect(api.createQuestion).toHaveBeenCalledWith("app-1", {
      question_text: "Seniority?",
      question_type: "SINGLE_CHOICE",
      required: true,
      max_length: 20,
      options: ["Junior", "Senior"],
    });
    expect(await screen.findByRole("article", { name: "Seniority?" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
  });

  it("refuses an empty question, a bad limit and a choice without options", async () => {
    await renderQuestions([]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add question" }));
    await user.click(screen.getByRole("button", { name: "Add question" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter the question.");

    await user.type(screen.getByLabelText("Question"), "Q");
    await user.type(screen.getByLabelText("Max characters"), "0");
    await user.click(screen.getByRole("button", { name: "Add question" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 1");

    await user.clear(screen.getByLabelText("Max characters"));
    await user.selectOptions(screen.getByLabelText("Answer type"), "MULTI_CHOICE");
    await user.click(screen.getByRole("button", { name: "Add question" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least one option");
    expect(api.createQuestion).not.toHaveBeenCalled();
  });

  it("shows the server's reason when a question cannot be added", async () => {
    await renderQuestions([]);
    api.createQuestion.mockRejectedValue(new ApiError(409, "QUESTION_EXISTS", "That key exists.", {}));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add question" }));
    await user.type(screen.getByLabelText("Question"), "Q");
    await user.click(screen.getByRole("button", { name: "Add question" }));
    expect(await screen.findByText("That key exists.")).toBeInTheDocument();
  });

  it("cancels adding", async () => {
    await renderQuestions([]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add question" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
  });

  it("edits a question", async () => {
    const question = makeQuestion({ question_text: WHY, max_length: 500 });
    await renderQuestions([question]);
    api.updateQuestion.mockResolvedValue({ ...question, question_text: "Why us?", max_length: 300 });
    const user = userEvent.setup();

    await user.click(card(WHY).getByRole("button", { name: "Edit question" }));
    const form = within(screen.getByRole("form", { name: "Edit question" }));
    await user.clear(form.getByLabelText("Question"));
    await user.type(form.getByLabelText("Question"), "Why us?");
    await user.clear(form.getByLabelText("Max characters"));
    await user.type(form.getByLabelText("Max characters"), "300");
    await user.click(form.getByRole("button", { name: "Save question" }));

    expect(api.updateQuestion).toHaveBeenCalledWith("app-1", question.id, {
      question_text: "Why us?",
      question_type: "FREE_TEXT",
      required: false,
      max_length: 300,
      options: null,
    });
    expect(await screen.findByRole("article", { name: "Why us?" })).toBeInTheDocument();
  });

  it("tells the user when a question change would no longer fit their answer", async () => {
    const question = makeQuestion({
      question_text: WHY,
      answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_answer: "x".repeat(80) }),
    });
    await renderQuestions([question]);
    api.updateQuestion.mockRejectedValue(
      new ApiError(409, "ANSWER_CONFLICT", "Your current answer would no longer fit this question.", {})
    );
    const user = userEvent.setup();
    await user.click(card(WHY).getByRole("button", { name: "Edit question" }));
    await user.click(screen.getByRole("button", { name: "Save question" }));
    expect(await screen.findByText(/would no longer fit/)).toBeInTheDocument();
    expect(yourAnswer(WHY).getByTestId("final-answer")).toHaveTextContent("x".repeat(80));
  });

  it("deletes a question only after confirmation", async () => {
    const question = makeQuestion({ question_text: WHY });
    await renderQuestions([question, makeQuestion({ question_text: "Other" })]);
    api.deleteQuestion.mockResolvedValue(undefined);
    const user = userEvent.setup();

    await user.click(card(WHY).getByRole("button", { name: "Delete question" }));
    expect(api.deleteQuestion).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Confirm" })).toHaveTextContent("cannot be undone");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(api.deleteQuestion).toHaveBeenCalledWith("app-1", question.id);
    await waitFor(() => expect(screen.queryByRole("article", { name: WHY })).not.toBeInTheDocument());
    expect(screen.getByRole("article", { name: "Other" })).toBeInTheDocument();
  });
});

describe("generating a suggestion", () => {
  it("offers Generate answer and Answer manually on an untouched question", async () => {
    await renderQuestions([makeQuestion({ question_text: WHY })]);
    expect(yourAnswer(WHY).getByText("Not answered yet.")).toBeInTheDocument();
    expect(yourAnswer(WHY).getByRole("button", { name: "Generate answer" })).toBeInTheDocument();
    expect(yourAnswer(WHY).getByRole("button", { name: "Answer manually" })).toBeInTheDocument();
  });

  it("shows the AI suggestion apart from the user's answer, with its sources", async () => {
    const question = makeQuestion({ question_text: WHY });
    await renderQuestions([question]);
    api.generateAnswer.mockResolvedValue({ ...question, answer: suggestedAnswer() });
    const user = userEvent.setup();

    await user.click(yourAnswer(WHY).getByRole("button", { name: "Generate answer" }));

    expect(api.generateAnswer).toHaveBeenCalledWith("app-1", question.id, false);
    const suggestion = within(await screen.findByRole("region", { name: "AI suggestion" }));
    expect(suggestion.getByTestId("suggestion-text")).toHaveTextContent("I develop SAP IS-U applications.");
    expect(suggestion.getByText(/Checked against your resume/)).toBeInTheDocument();
    expect(suggestion.getByText("Sources (1)")).toBeInTheDocument();
    for (const name of ["Accept", "Edit", "Regenerate", "Reject"]) {
      expect(suggestion.getByRole("button", { name })).toBeEnabled();
    }
    // the user's answer is still empty: a suggestion is not an answer
    expect(yourAnswer(WHY).getByText("Not answered yet.")).toBeInTheDocument();
    expect(card(WHY).getByTestId("answer-status")).toHaveTextContent("AI suggestion");
  });

  it("labels a suggestion built from the application's data, with no AI", async () => {
    const question = makeQuestion({
      question_text: "Salary expectations",
      answer: suggestedAnswer("78.000 EUR brutto pro Jahr", {
        suggestion_method: "DETERMINISTIC",
        ai_provenance: null,
      }),
    });
    await renderQuestions([question]);
    const suggestion = within(screen.getByRole("region", { name: "Suggestion from your application data" }));
    expect(suggestion.getByText("Built from your application data. No AI was used.")).toBeInTheDocument();
  });

  it("shows a loading state while generating", async () => {
    const question = makeQuestion({ question_text: WHY });
    await renderQuestions([question]);
    let finish: (value: Question) => void = () => {};
    api.generateAnswer.mockReturnValue(new Promise<Question>((resolve) => (finish = resolve)));
    const user = userEvent.setup();

    await user.click(yourAnswer(WHY).getByRole("button", { name: "Generate answer" }));
    expect(yourAnswer(WHY).getByRole("button", { name: "Generate answer" })).toBeDisabled();
    expect(yourAnswer(WHY).getByRole("button", { name: "Answer manually" })).toBeDisabled();
    finish({ ...question, answer: suggestedAnswer() });
    expect(await screen.findByRole("region", { name: "AI suggestion" })).toBeInTheDocument();
  });

  it("shows NEEDS_INPUT with the reason and still lets the user answer", async () => {
    const question = makeQuestion({ question_text: "Years of Java?", question_type: "NUMERIC" });
    await renderQuestions([question]);
    api.generateAnswer.mockResolvedValue({
      ...question,
      answer: makeAnswer({
        status: "NEEDS_INPUT",
        needs_input_reason: "FACTS_INSUFFICIENT",
        needs_input_message: "Your confirmed resume and application data don't establish an answer, so JobPilot won't guess.",
        ai_generation_id: "gen-1",
      }),
    });
    const user = userEvent.setup();
    await user.click(yourAnswer("Years of Java?").getByRole("button", { name: "Generate answer" }));

    expect(await screen.findByTestId("needs-input")).toHaveTextContent("won't guess");
    expect(card("Years of Java?").getByTestId("answer-status")).toHaveTextContent("Needs your input");
    expect(screen.queryByRole("region", { name: "AI suggestion" })).not.toBeInTheDocument();
    expect(yourAnswer("Years of Java?").getByRole("button", { name: "Answer manually" })).toBeEnabled();
    expect(yourAnswer("Years of Java?").getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("shows why a rejected AI answer was discarded, and changes nothing", async () => {
    const question = makeQuestion({ question_text: WHY });
    await renderQuestions([question]);
    api.generateAnswer.mockRejectedValue(
      new ApiError(502, "ANSWER_REJECTED", "The suggested answer contained claims your resume does not support.", {
        violations: [
          { code: "UNSUPPORTED_NUMBER", message: "m", location: "answer", excerpt: "12" },
        ],
      })
    );
    const user = userEvent.setup();
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Generate answer" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("does not support");
    expect(within(alert).getByLabelText("Why it was rejected")).toHaveTextContent("A number your resume doesn't state: “12”");
    expect(screen.queryByRole("region", { name: "AI suggestion" })).not.toBeInTheDocument();
    expect(yourAnswer(WHY).getByText("Not answered yet.")).toBeInTheDocument();
  });

  it("explains a blocked generation as information, not an error", async () => {
    const question = makeQuestion({ question_text: WHY });
    await renderQuestions([question]);
    api.generateAnswer.mockRejectedValue(new ApiError(409, "RESUME_MISSING", "Select or upload a resume first.", {}));
    const user = userEvent.setup();
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Generate answer" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Select or upload a resume first.");
  });

  it("flags a suggestion for a question that changed since", async () => {
    await renderQuestions([
      makeQuestion({ question_text: WHY, answer: suggestedAnswer(undefined, { suggestion_outdated: true }) }),
    ]);
    expect(card(WHY).getByText(/The question changed since this was suggested/)).toBeInTheDocument();
  });
});

describe("the user's decisions", () => {
  it("accepts the suggestion: it becomes the user's answer", async () => {
    const question = makeQuestion({ question_text: WHY, answer: suggestedAnswer("Because I like SAP.") });
    await renderQuestions([question]);
    api.acceptSuggestion.mockResolvedValue({
      ...question,
      answer: { ...question.answer, status: "ACCEPTED", answer_source: "AI", final_answer: "Because I like SAP." },
    });
    const user = userEvent.setup();
    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Accept" }));

    expect(api.acceptSuggestion).toHaveBeenCalledWith("app-1", question.id, false);
    await waitFor(() => expect(yourAnswer(WHY).getByTestId("final-answer")).toHaveTextContent("Because I like SAP."));
    expect(card(WHY).getByTestId("answer-status")).toHaveTextContent("Accepted");
    expect(yourAnswer(WHY).getByText(/accepted suggestion/)).toBeInTheDocument();
  });

  it("rejects the suggestion", async () => {
    const question = makeQuestion({ question_text: WHY, answer: suggestedAnswer() });
    await renderQuestions([question]);
    api.rejectSuggestion.mockResolvedValue({
      ...question,
      answer: { ...question.answer, status: "REJECTED", suggestion_dismissed: true },
    });
    const user = userEvent.setup();
    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Reject" }));

    expect(api.rejectSuggestion).toHaveBeenCalledWith("app-1", question.id);
    await waitFor(() => expect(card(WHY).getByTestId("answer-status")).toHaveTextContent("Rejected"));
    expect(screen.getByText(/rejected by you/)).toBeInTheDocument();
    // still answerable, and the rejected suggestion can still be taken back
    expect(yourAnswer(WHY).getByRole("button", { name: "Answer manually" })).toBeEnabled();
    expect(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Accept" })).toBeEnabled();
    expect(within(screen.getByRole("region", { name: "AI suggestion" })).queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("answers manually without any AI", async () => {
    const question = makeQuestion({ question_text: WHY });
    await renderQuestions([question]);
    api.saveAnswer.mockResolvedValue({
      ...question,
      answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_answer: "My own answer." }),
    });
    const user = userEvent.setup();

    await user.click(yourAnswer(WHY).getByRole("button", { name: "Answer manually" }));
    await user.type(yourAnswer(WHY).getByLabelText("Your answer"), "My own answer.");
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Save" }));

    expect(api.saveAnswer).toHaveBeenCalledWith("app-1", question.id, {
      answer: "My own answer.",
      based_on_suggestion: false,
    });
    expect(api.generateAnswer).not.toHaveBeenCalled();
    await waitFor(() => expect(yourAnswer(WHY).getByTestId("final-answer")).toHaveTextContent("My own answer."));
    expect(card(WHY).getByTestId("answer-status")).toHaveTextContent("Edited by you");
  });

  it("edits the suggestion into the user's answer, keeping the suggestion", async () => {
    const question = makeQuestion({ question_text: WHY, answer: suggestedAnswer("I have five years of experience.") });
    await renderQuestions([question]);
    api.saveAnswer.mockResolvedValue({
      ...question,
      answer: {
        ...question.answer,
        status: "USER_EDITED",
        answer_source: "HYBRID",
        final_answer: "I have around four years of experience.",
      },
    });
    const user = userEvent.setup();

    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Edit" }));
    const box = yourAnswer(WHY).getByLabelText("Your answer");
    expect(box).toHaveValue("I have five years of experience.");
    await user.clear(box);
    await user.type(box, "I have around four years of experience.");
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Save" }));

    expect(api.saveAnswer).toHaveBeenCalledWith("app-1", question.id, {
      answer: "I have around four years of experience.",
      based_on_suggestion: true,
    });
    // both stay visible and distinct
    await waitFor(() =>
      expect(yourAnswer(WHY).getByTestId("final-answer")).toHaveTextContent("around four years")
    );
    expect(screen.getByTestId("suggestion-text")).toHaveTextContent("five years");
  });

  it("clears the user's answer only after confirmation; the suggestion stays", async () => {
    const question = makeQuestion({
      question_text: WHY,
      answer: suggestedAnswer("Suggested.", {
        status: "USER_EDITED",
        answer_source: "USER",
        final_answer: "Mine.",
      }),
    });
    await renderQuestions([question]);
    api.clearAnswer.mockResolvedValue({
      ...question,
      answer: { ...question.answer, status: "SUGGESTED", answer_source: null, final_answer: null },
    });
    const user = userEvent.setup();
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Clear" }));
    expect(api.clearAnswer).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(api.clearAnswer).toHaveBeenCalledWith("app-1", question.id);
    await waitFor(() => expect(yourAnswer(WHY).getByText("Not answered yet.")).toBeInTheDocument());
    expect(screen.getByTestId("suggestion-text")).toHaveTextContent("Suggested.");
  });
});

describe("regenerating never overwrites the user's answer", () => {
  const edited = () =>
    makeQuestion({
      question_text: WHY,
      answer: suggestedAnswer("Old suggestion.", {
        status: "USER_EDITED",
        answer_source: "HYBRID",
        final_answer: "My edited answer.",
      }),
    });

  it("asks first when the answer was manually edited", async () => {
    const question = edited();
    await renderQuestions([question]);
    const user = userEvent.setup();
    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Regenerate" }));

    expect(api.regenerateAnswer).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Confirm" })).toHaveTextContent(
      "Your current answer has been manually edited. Generate a new AI suggestion without changing your current answer?"
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.regenerateAnswer).not.toHaveBeenCalled();
    expect(yourAnswer(WHY).getByTestId("final-answer")).toHaveTextContent("My edited answer.");
  });

  it("regenerates with the acknowledgement, and the user's answer survives", async () => {
    const question = edited();
    await renderQuestions([question]);
    api.regenerateAnswer.mockResolvedValue({
      ...question,
      answer: { ...question.answer, ai_suggestion: "A brand new suggestion.", ai_generation_id: "gen-2" },
    });
    const user = userEvent.setup();
    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Regenerate" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(api.regenerateAnswer).toHaveBeenCalledWith("app-1", question.id, true);
    await waitFor(() => expect(screen.getByTestId("suggestion-text")).toHaveTextContent("A brand new suggestion."));
    expect(yourAnswer(WHY).getByTestId("final-answer")).toHaveTextContent("My edited answer.");
    expect(card(WHY).getByTestId("answer-status")).toHaveTextContent("Edited by you");
  });

  it("asks before a new suggestion replaces the answer", async () => {
    const question = edited();
    await renderQuestions([question]);
    api.acceptSuggestion.mockResolvedValue({
      ...question,
      answer: { ...question.answer, status: "ACCEPTED", answer_source: "AI", final_answer: "Old suggestion." },
    });
    const user = userEvent.setup();
    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Accept" }));
    expect(api.acceptSuggestion).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Confirm" })).toHaveTextContent("Your current answer will be replaced");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(api.acceptSuggestion).toHaveBeenCalledWith("app-1", question.id, true);
  });

  it("needs no confirmation when there is no answer to protect", async () => {
    const question = makeQuestion({ question_text: WHY, answer: suggestedAnswer() });
    await renderQuestions([question]);
    api.regenerateAnswer.mockResolvedValue({ ...question, answer: suggestedAnswer("Newer.") });
    const user = userEvent.setup();
    await user.click(within(screen.getByRole("region", { name: "AI suggestion" })).getByRole("button", { name: "Regenerate" }));
    expect(api.regenerateAnswer).toHaveBeenCalledWith("app-1", question.id, false);
  });
});

describe("copying and the character counter", () => {
  it("copies the exact final answer", async () => {
    const question = makeQuestion({
      question_text: WHY,
      answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_answer: "Line one.\nLine two." }),
    });
    await renderQuestions([question]);
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("Line one.\nLine two.");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("copies the selected options of a choice answer", async () => {
    const question = makeQuestion({
      question_text: "Which?",
      question_type: "MULTI_CHOICE",
      options: ["ABAP", "SQL"],
      answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_selected_options: ["ABAP", "SQL"] }),
    });
    await renderQuestions([question]);
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(yourAnswer("Which?").getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("ABAP, SQL");
  });

  it("counts the characters remaining and blocks saving over the limit", async () => {
    const question = makeQuestion({ question_text: WHY, max_length: 10 });
    await renderQuestions([question]);
    const user = userEvent.setup();
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Answer manually" }));
    const box = yourAnswer(WHY).getByLabelText("Your answer");

    expect(yourAnswer(WHY).getByTestId("character-counter")).toHaveTextContent("10 of 10 characters remaining");
    await user.type(box, "12345");
    expect(yourAnswer(WHY).getByTestId("character-counter")).toHaveTextContent("5 of 10 characters remaining");
    await user.type(box, "6789012");
    expect(yourAnswer(WHY).getByTestId("character-counter")).toHaveTextContent("2 characters over the limit of 10");
    expect(yourAnswer(WHY).getByTestId("character-counter")).toHaveTextContent("never cuts it for you");
    expect(yourAnswer(WHY).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(box).toHaveValue("123456789012"); // never truncated
    await user.type(box, "{Backspace}{Backspace}");
    expect(yourAnswer(WHY).getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("shows no counter when there is no limit", async () => {
    await renderQuestions([makeQuestion({ question_text: WHY })]);
    const user = userEvent.setup();
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Answer manually" }));
    expect(screen.queryByTestId("character-counter")).not.toBeInTheDocument();
  });
});

describe("choice questions", () => {
  it("picks one option of a single choice question", async () => {
    const question = makeQuestion({
      question_text: "Seniority?",
      question_type: "SINGLE_CHOICE",
      options: ["Junior", "Mid-level", "Senior"],
    });
    await renderQuestions([question]);
    api.saveAnswer.mockResolvedValue({
      ...question,
      answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_selected_options: ["Senior"] }),
    });
    const user = userEvent.setup();
    await user.click(yourAnswer("Seniority?").getByRole("button", { name: "Answer manually" }));
    expect(yourAnswer("Seniority?").getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(yourAnswer("Seniority?").getByLabelText("Junior"));
    await user.click(yourAnswer("Seniority?").getByLabelText("Senior"));
    await user.click(yourAnswer("Seniority?").getByRole("button", { name: "Save" }));

    expect(api.saveAnswer).toHaveBeenCalledWith("app-1", question.id, {
      selected_options: ["Senior"],
      based_on_suggestion: false,
    });
    await waitFor(() => expect(yourAnswer("Seniority?").getByTestId("final-answer")).toHaveTextContent("Senior"));
  });

  it("picks several options of a multiple choice question", async () => {
    const question = makeQuestion({
      question_text: "Which?",
      question_type: "MULTI_CHOICE",
      options: ["ABAP", "SQL", "Python"],
    });
    await renderQuestions([question]);
    api.saveAnswer.mockResolvedValue({ ...question, answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_selected_options: ["ABAP", "SQL"] }) });
    const user = userEvent.setup();
    await user.click(yourAnswer("Which?").getByRole("button", { name: "Answer manually" }));
    await user.click(yourAnswer("Which?").getByLabelText("ABAP"));
    await user.click(yourAnswer("Which?").getByLabelText("SQL"));
    await user.click(yourAnswer("Which?").getByLabelText("Python"));
    await user.click(yourAnswer("Which?").getByLabelText("Python"));
    await user.click(yourAnswer("Which?").getByRole("button", { name: "Save" }));
    expect(api.saveAnswer).toHaveBeenCalledWith("app-1", question.id, {
      selected_options: ["ABAP", "SQL"],
      based_on_suggestion: false,
    });
  });

  it("shows a suggested option as the suggestion and accepts it", async () => {
    const question = makeQuestion({
      question_text: "Seniority?",
      question_type: "SINGLE_CHOICE",
      options: ["Junior", "Senior"],
      answer: suggestedAnswer("", { ai_suggestion: null, ai_selected_options: ["Senior"] }),
    });
    await renderQuestions([question]);
    expect(screen.getByTestId("suggestion-text")).toHaveTextContent("Senior");
  });

  it("answers a yes/no question with its own labels", async () => {
    const question = makeQuestion({
      question_text: "Remote?",
      question_type: "YES_NO",
      options: ["Ja", "Nein"],
    });
    await renderQuestions([question]);
    api.saveAnswer.mockResolvedValue({ ...question, answer: makeAnswer({ status: "USER_EDITED", answer_source: "USER", final_answer: "Nein" }) });
    const user = userEvent.setup();
    await user.click(yourAnswer("Remote?").getByRole("button", { name: "Answer manually" }));
    await user.click(yourAnswer("Remote?").getByLabelText("Nein"));
    await user.click(yourAnswer("Remote?").getByRole("button", { name: "Save" }));
    expect(api.saveAnswer).toHaveBeenCalledWith("app-1", question.id, { answer: "Nein", based_on_suggestion: false });
  });

  it("shows the server's reason when an answer is refused", async () => {
    const question = makeQuestion({ question_text: WHY, max_length: 10 });
    await renderQuestions([question]);
    api.saveAnswer.mockRejectedValue(new ApiError(422, "ANSWER_TOO_LONG", "The answer has 11 characters; this question allows 10.", {}));
    const user = userEvent.setup();
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Answer manually" }));
    await user.type(yourAnswer(WHY).getByLabelText("Your answer"), "1234567890");
    await user.click(yourAnswer(WHY).getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("this question allows 10");
    // the text typed is still there
    expect(yourAnswer(WHY).getByLabelText("Your answer")).toHaveValue("1234567890");
  });
});
