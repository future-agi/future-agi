import { buildJevMapping } from "src/sections/evals/utils/jevMapping";

export const INSTANT_EVAL_TAG = "instant-eval";
export const INSTANT_EVAL_MODEL = "jev-latest";
export const YES_NO_LABELS = ["Yes", "No"];
export const MAX_QUESTION_LENGTH = 2000;
export const MAX_CATEGORIES = 10;
export const MAX_QUESTIONS = 5;

export const ANSWER_TYPES = {
  YES_NO: "yes_no",
  CATEGORY: "category",
};

// What Jev reads from each row: each choice names the template variables its
// instructions use and the eval mapping that fills them. Traces and spans
// resolve input/output on the trace or the span itself; trace_tree is
// every span of the trace as an indented outline (kind, name, model, latency,
// errors, clipped input/output). A session's transcript is its user and
// assistant messages only; transcript_full adds every turn's tool calls,
// their results and errors, and is what a session is read as by default:
// read without them, "was it resolved?" is answered Yes where a tool failed.
export const READS = {
  input_output: {
    label: "Input and output",
    hint: "The trace's input and its final output",
    variables: ["input", "output"],
    mapping: { input: "input", output: "output" },
  },
  // The variable can't be called `trace`: the trace eval runner drops a
  // kwarg of that name and Jev reports it missing.
  trace_tree: {
    label: "The whole trace",
    hint: "Every span: LLM calls, tool calls and results, retrieval, errors",
    variables: ["whole_trace"],
    mapping: { whole_trace: "trace_tree" },
  },
  input: {
    label: "Input only",
    hint: "The trace's input",
    variables: ["input"],
    mapping: { input: "input" },
  },
  output: {
    label: "Output only",
    hint: "The trace's final output",
    variables: ["output"],
    mapping: { output: "output" },
  },
  transcript: {
    label: "The conversation only",
    hint: "User and assistant messages, without tool calls or their results",
    variables: ["transcript"],
    mapping: { transcript: "transcript" },
  },
  transcript_full: {
    label: "The whole session",
    hint: "Every turn with its tool calls, their results and errors",
    variables: ["conversation"],
    mapping: { conversation: "transcript_full" },
  },
};

// A span is read on its own: the same fields, worded for one span.
const SPAN_READS = {
  input_output: {
    label: "Span input and output",
    hint: "This span's own input and output",
  },
  input: { label: "Input only", hint: "This span's own input" },
  output: { label: "Output only", hint: "This span's own output" },
};

// The choices each view offers, the first being the default.
export const UNIT_READS = {
  traces: ["input_output", "trace_tree", "input", "output"],
  spans: ["input_output", "input", "output"],
  sessions: ["transcript_full", "transcript"],
  users: ["transcript_full", "transcript"],
};

export const readsFor = (unit) => UNIT_READS[unit] || UNIT_READS.traces;

export const readOption = (unit, reads) => {
  const base = READS[reads] || READS.input_output;
  return unit === "spans" && SPAN_READS[reads]
    ? { ...base, ...SPAN_READS[reads] }
    : base;
};

const VARIABLE_LABELS = {
  traces: { input: "Trace input", output: "Trace output" },
  spans: { input: "Span input", output: "Span output" },
};
const SHARED_VARIABLE_LABELS = {
  whole_trace: "Trace",
  transcript: "Conversation",
  conversation: "Conversation",
};

const variableLabel = (variable, unit) =>
  VARIABLE_LABELS[unit]?.[variable] ||
  SHARED_VARIABLE_LABELS[variable] ||
  VARIABLE_LABELS.traces[variable] ||
  variable;

export const cleanCategories = (categories = []) => {
  const seen = new Set();
  return categories
    .map((category) => (typeof category === "string" ? category.trim() : ""))
    .filter((category) => {
      const key = category.toLowerCase();
      if (!category || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
};

export const answerLabels = ({ answerType, categories }) =>
  answerType === ANSWER_TYPES.CATEGORY
    ? cleanCategories(categories)
    : YES_NO_LABELS;

// Category definitions are Jev's decision rule for each label (sent as the
// choice criteria). Labels without one are left for Jev to read literally.
export const MAX_DEFINITION_LENGTH = 2000;

export const cleanDefinitions = (definitions = {}, labels = []) =>
  Object.fromEntries(
    labels
      .map((label) => [
        label,
        `${definitions?.[label] ?? ""}`.trim().slice(0, MAX_DEFINITION_LENGTH),
      ])
      .filter(([, definition]) => definition),
  );

export function validateInstantEval(
  { question, answerType, categories },
  noun = "trace",
) {
  const text = (question || "").trim();
  if (!text) return `Write the question Jev should answer for each ${noun}.`;
  if (text.length > MAX_QUESTION_LENGTH)
    return `Keep the question under ${MAX_QUESTION_LENGTH} characters.`;
  if (answerType === ANSWER_TYPES.CATEGORY) {
    const labels = cleanCategories(categories);
    if (labels.length < 2) return "Add at least two categories.";
    if (labels.length > MAX_CATEGORIES)
      return `Use at most ${MAX_CATEGORIES} categories.`;
    if (labels.some((label) => label.startsWith("__")))
      return "Category names can't start with two underscores.";
    if (labels.some((label) => label.length > 200))
      return "Keep each category under 200 characters.";
  }
  return null;
}

// Template names allow only lowercase letters, numbers, "-" and "_", and are
// unique per workspace, so a time suffix keeps repeated questions distinct.
export function evaluatorName(question, now = new Date()) {
  const slug = question
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 8)
    .join("-");
  const stamp = now.toISOString().replace(/\D/g, "").slice(4, 14);
  return `${slug || "instant-eval"}-${stamp}`.slice(0, 255);
}

export function buildInstructions(question, reads, unit = "traces") {
  const variables = (READS[reads] || READS.input_output).variables;
  const context = variables
    .map((variable) => `${variableLabel(variable, unit)}:\n{{${variable}}}`)
    .join("\n\n");
  return `${question.trim()}\n\n${context}`;
}

// Yes/No questions are a two-label choice so results read "Yes" / "No" in the
// grid and filters, rather than Passed/Failed. "Yes" scores 1, so the saved
// evaluator's pass rate is the share of traces where the answer was yes.
export function buildTemplatePayload({
  question,
  answerType,
  categories,
  definitions,
  reads,
  unit,
  now,
}) {
  const labels = answerLabels({ answerType, categories });
  // Categories carry no pass/fail meaning, so each scores 1.
  const choiceScores = Object.fromEntries(
    labels.map((label) => [
      label,
      answerType === ANSWER_TYPES.CATEGORY || label === "Yes" ? 1 : 0,
    ]),
  );
  return {
    name: evaluatorName(question, now),
    eval_type: "llm",
    instructions: buildInstructions(question, reads, unit),
    model: INSTANT_EVAL_MODEL,
    output_type: "deterministic",
    choice_scores: choiceScores,
    pass_threshold: 0.5,
    multi_choice: false,
    description: question.trim(),
    tags: [INSTANT_EVAL_TAG],
    template_format: "mustache",
    jev_mapping: buildJevMapping("deterministic", choiceScores, {
      choice: { descriptions: cleanDefinitions(definitions, labels) },
    }),
  };
}

// Template variable -> the row field the backend resolves for it.
export const buildEvalMapping = (reads) => ({
  ...(READS[reads] || READS.input_output).mapping,
});
