// KH-SCOPE §6 B1: the one review rule (form, rubric card and server).
import { describe, expect, it } from "vitest";
import {
  labelProblems,
  numberProblem,
  numericRule,
  problemsText,
  ruleText,
  valueProblem,
} from "../label-rules";

const helpfulness = {
  label_id: "l-help",
  name: "Helpfulness",
  type: "numeric",
  settings: { min: 0, max: 1, step_size: 0.1 },
};
const quality = {
  label_id: "l-quality",
  name: "Quality",
  type: "star",
  settings: { no_of_stars: 5 },
};
const tone = {
  label_id: "l-tone",
  name: "Tone",
  type: "thumbs_up_down",
  settings: {},
};
const preference = {
  label_id: "l-pref",
  name: "Overall preference",
  type: "categorical",
  settings: {
    options: [{ label: "A" }, { label: "Tie" }, { label: "B" }],
    multi_choice: false,
  },
};
const summary = {
  id: "l-summary", // annotation labels key by `id`
  name: "Summary",
  type: "text",
  settings: { min_length: 5, max_length: 12 },
};

describe("labelProblems: every label needs a value", () => {
  it("names each empty label, in label order, with its id", () => {
    const problems = labelProblems([tone, helpfulness, quality], {
      "l-quality": { rating: 0 },
    });
    expect(problems.map((p) => [p.label_id, p.kind, p.text])).toEqual([
      ["l-tone", "empty", "Tone needs a score."],
      ["l-help", "empty", "Helpfulness needs a score."],
      ["l-quality", "empty", "Quality needs a score."],
    ]);
  });

  it("reads annotation labels by `id` and queue labels by `label_id`", () => {
    expect(
      labelProblems([summary], { "l-summary": { text: "fine." } }),
    ).toEqual([]);
    expect(labelProblems([tone], { "l-tone": { value: "up" } })).toEqual([]);
  });

  it("is empty when every value fits", () => {
    expect(
      labelProblems([helpfulness, quality, tone, preference], {
        "l-help": { value: 0.3 },
        "l-quality": { rating: 4 },
        "l-tone": { value: "down" },
        "l-pref": { selected: ["Tie"] },
      }),
    ).toEqual([]);
  });

  it("a refused entry still in its field is the label's problem, not its kept value", () => {
    const typed = numberProblem(
      { name: "Helpfulness", settings: helpfulness.settings },
      1.4,
    );
    const problems = labelProblems(
      [tone, helpfulness, quality],
      { "l-help": { value: 0.5 }, "l-quality": { rating: 4 } },
      { "l-help": typed },
    );
    expect(problems).toEqual([
      {
        label_id: "l-tone",
        name: "Tone",
        kind: "empty",
        text: "Tone needs a score.",
      },
      {
        label_id: "l-help",
        name: "Helpfulness",
        kind: "range",
        text: "Helpfulness must be between 0 and 1 (you entered 1.4).",
      },
    ]);
    expect(problemsText(problems.slice(1))).toBe(
      "Fix 1 score before submitting: Helpfulness must be between 0 and 1 (you entered 1.4).",
    );
    // With no refusal, the kept 0.5 passes.
    expect(
      labelProblems([helpfulness], { "l-help": { value: 0.5 } }, {}),
    ).toEqual([]);
  });
});

describe("numeric: range and step", () => {
  it("refuses outside min..max with the value entered", () => {
    expect(numberProblem(helpfulness, 1.4)).toMatchObject({
      kind: "range",
      text: "Helpfulness must be between 0 and 1 (you entered 1.4).",
    });
    expect(numberProblem(helpfulness, -0.2).text).toBe(
      "Helpfulness must be between 0 and 1 (you entered -0.2).",
    );
  });

  it("refuses off-step values from min, within 1e-9", () => {
    expect(numberProblem(helpfulness, 0.35)).toMatchObject({
      kind: "step",
      text: "Helpfulness moves in steps of 0.1 (you entered 0.35).",
    });
    // 0.1 + 0.2 and 0.7 are float-noisy multiples of 0.1: allowed.
    expect(numberProblem(helpfulness, 0.1 + 0.2)).toBeNull();
    expect(numberProblem(helpfulness, 0.7)).toBeNull();
    expect(numberProblem(helpfulness, 0)).toBeNull();
    expect(numberProblem(helpfulness, 1)).toBeNull();
  });

  it("counts steps from min and always allows the max (backend rule)", () => {
    const odd = { name: "Odd", settings: { min: 1, max: 10, step_size: 2 } };
    expect(numberProblem(odd, 3)).toBeNull();
    expect(numberProblem(odd, 10)).toBeNull(); // the max, off-step
    expect(numberProblem(odd, 4)?.kind).toBe("step");
  });

  it("uses the input's defaults (0 – 10, steps of 1) and `step` too", () => {
    expect(numericRule({})).toEqual({ min: 0, max: 10, step: 1 });
    expect(numericRule({ min: 2, max: 4, step: 0.5 })).toEqual({
      min: 2,
      max: 4,
      step: 0.5,
    });
    expect(numberProblem({ name: "Score", settings: {} }, 11)?.text).toBe(
      "Score must be between 0 and 10 (you entered 11).",
    );
  });

  it("refuses a value that is not a number", () => {
    expect(valueProblem(helpfulness, { value: "abc" })?.text).toBe(
      "Helpfulness must be between 0 and 1 (you entered abc).",
    );
    expect(valueProblem(helpfulness, { value: "0.5" })).toBeNull();
  });
});

describe("stars, choices, thumbs and text", () => {
  it("stars: a whole number from 1 to no_of_stars", () => {
    expect(valueProblem(quality, { rating: 6 })).toMatchObject({
      kind: "stars",
      text: "Quality is 1 to 5 stars.",
    });
    expect(valueProblem(quality, { rating: 2.5 })?.kind).toBe("stars");
    expect(valueProblem(quality, { rating: 5 })).toBeNull();
  });

  it("categorical: configured options, one unless multi_choice", () => {
    expect(valueProblem(preference, { selected: ["C"] })).toMatchObject({
      kind: "choice",
      text: "Overall preference must be one of A, Tie, B.",
    });
    expect(valueProblem(preference, { selected: ["A", "B"] })?.text).toBe(
      "Overall preference takes one choice (you picked A, B).",
    );
    const multi = {
      ...preference,
      settings: { ...preference.settings, multi_choice: true },
    };
    expect(valueProblem(multi, { selected: ["A", "B"] })).toBeNull();
  });

  it("thumbs: up or down only", () => {
    expect(valueProblem(tone, { value: "sideways" })).toMatchObject({
      kind: "choice",
      text: "Tone must be Yes or No.",
    });
  });

  it("text: min and max length", () => {
    expect(valueProblem(summary, { text: "ok" })).toMatchObject({
      kind: "length",
      text: "Summary needs at least 5 characters (you wrote 2).",
    });
    expect(valueProblem(summary, { text: "far too long text" })?.text).toBe(
      "Summary takes at most 12 characters (you wrote 17).",
    );
  });
});

describe("ruleText and problemsText (the copy)", () => {
  it("shows each label's rule", () => {
    expect(ruleText(helpfulness)).toBe("0 – 1 · steps of 0.1");
    expect(ruleText(quality)).toBe("1 – 5 stars");
    expect(ruleText(preference)).toBe("One of A, Tie, B");
    expect(ruleText(tone)).toBe("Yes or No");
    expect(ruleText(summary)).toBe("5 – 12 characters");
    expect(ruleText({ type: "text", settings: {} })).toBeNull();
  });

  it("joins the problems into one sentence", () => {
    const problems = labelProblems([tone, helpfulness], {
      "l-help": { value: 1.4 },
    });
    expect(problemsText(problems)).toBe(
      "Fix 2 scores before submitting: Tone needs a score. Helpfulness must be between 0 and 1 (you entered 1.4).",
    );
    expect(problemsText(problems.slice(0, 1))).toBe(
      "Fix 1 score before submitting: Tone needs a score.",
    );
    expect(problemsText([])).toBe("");
  });
});
