// An answer left as it was loaded is not checked again against limits its
// label gained since; a changed answer, or an empty one, still is.
import { describe, expect, it } from "vitest";
import { labelProblems } from "../label-rules";

const COMMENT = {
  label_id: "comment",
  name: "Comment",
  type: "text",
  settings: { min_length: 50, max_length: 1000 },
};
const ACCURACY = {
  label_id: "accuracy",
  name: "Accuracy",
  type: "numeric",
  settings: { min: 0, max: 5, step_size: 1 },
};
const TONE = {
  label_id: "tone",
  name: "Tone",
  type: "categorical",
  settings: { options: [{ label: "positive" }, { label: "negative" }] },
};
const QUALITY = {
  label_id: "quality",
  name: "Quality",
  type: "star",
  settings: { no_of_stars: 3 },
};
const LABELS = [COMMENT, ACCURACY, TONE, QUALITY];
const LOADED = {
  comment: { text: "short note" },
  accuracy: { value: 8 },
  tone: { selected: ["neutral"] },
  quality: { rating: 5 },
};

describe("labelProblems with the loaded answers", () => {
  it("passes every answer left as it was loaded", () => {
    expect(labelProblems(LABELS, { ...LOADED }, {}, LOADED)).toEqual([]);
  });

  it("checks an answer changed to another value that breaks the limits", () => {
    const values = { ...LOADED, comment: { text: "still far too short." } };
    expect(labelProblems(LABELS, values, {}, LOADED)).toEqual([
      {
        label_id: "comment",
        name: "Comment",
        kind: "length",
        text: "Comment needs at least 50 characters (you wrote 20).",
      },
    ]);
  });

  it("still refuses an emptied answer", () => {
    const values = { ...LOADED, comment: { text: "" } };
    expect(labelProblems(LABELS, values, {}, LOADED)).toEqual([
      {
        label_id: "comment",
        name: "Comment",
        kind: "empty",
        text: "Comment needs a score.",
      },
    ]);
  });

  it("checks a first answer that breaks the limits", () => {
    const values = { ...LOADED, comment: { text: "short note" } };
    const loaded = { ...LOADED, comment: undefined };
    expect(labelProblems(LABELS, values, {}, loaded)).toHaveLength(1);
    expect(labelProblems(LABELS, values, {})).toHaveLength(4);
  });

  it("a refused typed entry still wins over the loaded answer", () => {
    const refused = {
      accuracy: {
        label_id: "accuracy",
        name: "Accuracy",
        kind: "range",
        text: "Accuracy must be between 0 and 5 (you entered 9).",
      },
    };
    expect(labelProblems(LABELS, { ...LOADED }, refused, LOADED)).toEqual([
      refused.accuracy,
    ]);
  });
});

describe("an answer counts as unchanged the way it is stored", () => {
  const only = (label, value, loaded) =>
    labelProblems(
      [label],
      { [label.label_id]: value },
      {},
      { [label.label_id]: loaded },
    );

  it("a loaded bare answer equals the same answer in its field shape", () => {
    expect(only(COMMENT, { text: "short note" }, "short note")).toEqual([]);
    expect(only(ACCURACY, { value: 8 }, 8)).toEqual([]);
    expect(only(TONE, { selected: ["neutral"] }, "neutral")).toEqual([]);
  });

  it("the same choices in another order are unchanged", () => {
    const multi = {
      ...TONE,
      settings: { ...TONE.settings, multi_choice: true },
    };
    expect(
      only(
        multi,
        { selected: ["neutral", "mixed"] },
        { selected: ["mixed", "neutral"] },
      ),
    ).toEqual([]);
  });

  it("text with a character added is a changed answer", () => {
    expect(
      only(COMMENT, { text: "short note!" }, { text: "short note" }),
    ).toHaveLength(1);
  });
});
