import { describe, expect, it } from "vitest";
import {
  ANSWER_TYPES,
  INSTANT_EVAL_MODEL,
  INSTANT_EVAL_TAG,
  buildEvalMapping,
  buildInstructions,
  buildTemplatePayload,
  cleanCategories,
  evaluatorName,
  readOption,
  readsFor,
  validateInstantEval,
} from "../instantEvalPayloads";

const NOW = new Date("2026-10-15T04:30:00Z");

describe("validateInstantEval", () => {
  it("requires a question", () => {
    expect(
      validateInstantEval({ question: "  ", answerType: ANSWER_TYPES.YES_NO }),
    ).toMatch(/Write the question/);
    expect(
      validateInstantEval(
        { question: "", answerType: ANSWER_TYPES.YES_NO },
        "session",
      ),
    ).toBe("Write the question Jev should answer for each session.");
  });

  it("requires two distinct categories for category questions", () => {
    expect(
      validateInstantEval({
        question: "What is this about?",
        answerType: ANSWER_TYPES.CATEGORY,
        categories: ["Billing", " billing "],
      }),
    ).toMatch(/at least two categories/);
    expect(
      validateInstantEval({
        question: "What is this about?",
        answerType: ANSWER_TYPES.CATEGORY,
        categories: ["Billing", "Shipping"],
      }),
    ).toBeNull();
  });

  it("caps categories at ten", () => {
    expect(
      validateInstantEval({
        question: "What is this about?",
        answerType: ANSWER_TYPES.CATEGORY,
        categories: Array.from({ length: 11 }, (_, i) => `c${i}`),
      }),
    ).toMatch(/at most 10/);
  });
});

describe("cleanCategories", () => {
  it("trims, drops blanks and de-duplicates case-insensitively", () => {
    expect(cleanCategories([" Refund", "refund", "", "Shipping "])).toEqual([
      "Refund",
      "Shipping",
    ]);
  });
});

describe("buildTemplatePayload", () => {
  it("builds a Yes/No question as a two-label Jev choice", () => {
    const payload = buildTemplatePayload({
      question: "Is the customer frustrated?",
      answerType: ANSWER_TYPES.YES_NO,
      reads: "input_output",
      now: NOW,
    });
    expect(payload).toMatchObject({
      eval_type: "llm",
      model: INSTANT_EVAL_MODEL,
      output_type: "deterministic",
      choice_scores: { Yes: 1, No: 0 },
      tags: [INSTANT_EVAL_TAG],
      description: "Is the customer frustrated?",
      multi_choice: false,
    });
    expect(payload.jev_mapping).toMatchObject({
      revision: "jev-map-v1",
      question_type: "choice",
      choice: { labels: ["Yes", "No"] },
    });
    expect(payload.instructions).toContain("{{input}}");
    expect(payload.instructions).toContain("{{output}}");
  });

  it("builds a category question with every category as a label", () => {
    const payload = buildTemplatePayload({
      question: "What is the customer asking about?",
      answerType: ANSWER_TYPES.CATEGORY,
      categories: ["Refund", "Shipping", "refund"],
      reads: "input",
      now: NOW,
    });
    expect(payload.choice_scores).toEqual({ Refund: 1, Shipping: 1 });
    expect(payload.jev_mapping.choice.labels).toEqual(["Refund", "Shipping"]);
    expect(payload.instructions).toContain("{{input}}");
    expect(payload.instructions).not.toContain("{{output}}");
  });
});

describe("helpers", () => {
  it("maps template variables to the root span fields Jev reads", () => {
    expect(buildEvalMapping("input_output")).toEqual({
      input: "input",
      output: "output",
    });
    expect(buildEvalMapping("output")).toEqual({ output: "output" });
  });

  it("puts the question first and labels each variable", () => {
    expect(buildInstructions("Is it urgent?", "output")).toBe(
      "Is it urgent?\n\nTrace output:\n{{output}}",
    );
  });

  it("reads a session as its whole conversation", () => {
    expect(buildInstructions("Did they get it?", "transcript")).toBe(
      "Did they get it?\n\nConversation:\n{{transcript}}",
    );
    expect(buildEvalMapping("transcript")).toEqual({
      transcript: "transcript",
    });
    const payload = buildTemplatePayload({
      question: "Did the customer get what they came for?",
      answerType: ANSWER_TYPES.YES_NO,
      reads: "transcript",
      now: NOW,
    });
    expect(payload.instructions).toContain("{{transcript}}");
    expect(payload.instructions).not.toContain("{{input}}");
  });

  it("reads a whole trace as one outline of its spans", () => {
    expect(buildEvalMapping("trace_tree")).toEqual({
      whole_trace: "trace_tree",
    });
    expect(buildInstructions("Did a tool fail?", "trace_tree")).toBe(
      "Did a tool fail?\n\nTrace:\n{{whole_trace}}",
    );
    expect(readOption("traces", "trace_tree")).toMatchObject({
      label: "The whole trace",
      hint: "Every span: LLM calls, tool calls and results, retrieval, errors",
    });
  });

  it("reads a whole session with every turn's steps", () => {
    expect(buildEvalMapping("transcript_full")).toEqual({
      conversation: "transcript_full",
    });
    expect(buildInstructions("Was it resolved?", "transcript_full")).toBe(
      "Was it resolved?\n\nConversation:\n{{conversation}}",
    );
    expect(readOption("users", "transcript_full").label).toBe(
      "The whole session",
    );
  });

  it("words a span's input and output as the span's own", () => {
    expect(buildInstructions("On topic?", "input_output", "spans")).toBe(
      "On topic?\n\nSpan input:\n{{input}}\n\nSpan output:\n{{output}}",
    );
    expect(readOption("spans", "input_output").label).toBe(
      "Span input and output",
    );
    expect(
      buildTemplatePayload({
        question: "On topic?",
        answerType: ANSWER_TYPES.YES_NO,
        reads: "output",
        unit: "spans",
        now: NOW,
      }).instructions,
    ).toBe("On topic?\n\nSpan output:\n{{output}}");
  });

  it("offers each view its own reads, the default first", () => {
    expect(readsFor("traces")).toEqual([
      "input_output",
      "trace_tree",
      "input",
      "output",
    ]);
    expect(readsFor("spans")).toEqual(["input_output", "input", "output"]);
    // A session is read whole by default: without its tool results, "was
    // it resolved?" reads Yes where a tool call failed.
    expect(readsFor("sessions")).toEqual(["transcript_full", "transcript"]);
    expect(readsFor("users")).toEqual(["transcript_full", "transcript"]);
    expect(readOption("sessions", "transcript")).toMatchObject({
      label: "The conversation only",
      hint: "User and assistant messages, without tool calls or their results",
    });
    expect(readOption("sessions", "transcript_full").hint).toBe(
      "Every turn with its tool calls, their results and errors",
    );
  });

  it("names evaluators with a slug the template API accepts", () => {
    expect(
      evaluatorName("Did the agent promise a refund, without checking?", NOW),
    ).toBe("did-the-agent-promise-a-refund-without-checking-1015043000");
  });
});
