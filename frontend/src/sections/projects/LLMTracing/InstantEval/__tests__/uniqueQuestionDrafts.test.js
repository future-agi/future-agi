import { describe, expect, it } from "vitest";
import { uniqueQuestionDrafts } from "../instantEvalPayloads";

const question = {
  question: "Was the issue resolved?",
  answerType: "yes_no",
  categories: [],
};

// A run shares one unit and read mode across all its draft editors.
describe("uniqueQuestionDrafts", () => {
  it("merges manually typed copies using the saved-question matching rules", () => {
    const duplicate = {
      ...question,
      question: "  WAS THE ISSUE RESOLVED?  ",
      definitions: { Yes: "", unused: "ignored" },
    };
    const other = { ...question, question: "Was a refund requested?" };
    const drafts = [question, duplicate, other, { ...other }];
    expect(uniqueQuestionDrafts(drafts)).toEqual([question, other]);
    expect(drafts).toHaveLength(4);
  });

  it("keeps questions with different answer types, labels or definitions", () => {
    const category = {
      ...question,
      answerType: "category",
      categories: ["Resolved", "Unresolved"],
    };
    const drafts = [
      question,
      category,
      { ...category, categories: ["Resolved", "Escalated"] },
      { ...category, definitions: { Resolved: "Confirmed by the customer" } },
      { ...question, definitions: { Yes: "Confirmed by the customer" } },
    ];
    expect(uniqueQuestionDrafts(drafts)).toEqual(drafts);
  });

  it("normalizes category labels and definitions without depending on key order", () => {
    const category = {
      ...question,
      answerType: "category",
      categories: ["Resolved", "Unresolved"],
      definitions: { Resolved: "Confirmed", Unresolved: "Still open" },
    };
    const duplicate = {
      ...category,
      categories: [" Resolved ", "Unresolved", "Resolved"],
      definitions: { Unresolved: " Still open ", Resolved: "Confirmed" },
    };
    expect(uniqueQuestionDrafts([category, duplicate])).toEqual([category]);
  });

  it("keeps blank editors so their validation is not bypassed", () => {
    const blank = { ...question, question: "" };
    expect(uniqueQuestionDrafts([blank, { ...blank }])).toHaveLength(2);
  });
});
