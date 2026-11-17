// W6 B5: the one shape of a retrieved variable (card, create body, run).
import { describe, expect, it } from "vitest";
import {
  contextSourceIssues,
  contextSourceOf,
  contextSourceSummary,
  retrievedText,
} from "../contextSourceModel";

describe("contextSourceModel", () => {
  it("normalizes a complete source and refuses a partial one", () => {
    expect(
      contextSourceOf({
        variable: " context ",
        kbId: "kb1",
        topK: "3",
        queryColumn: "c1",
      }),
    ).toEqual({
      variable: "context",
      kb_id: "kb1",
      top_k: 3,
      query_column: "c1",
    });
    expect(
      contextSourceOf({
        variable: "context",
        kb_id: "kb1",
        query_column: "c1",
      }),
    ).toBeNull();
    expect(
      contextSourceOf({
        variable: "context",
        kb_id: "kb1",
        top_k: 0,
        query_column: "c1",
      }),
    ).toBeNull();
    expect(contextSourceOf(null)).toBeNull();
  });

  it("names what blocks it, in order", () => {
    expect(contextSourceIssues(null)).toEqual([]);
    expect(
      contextSourceIssues(
        { variable: "ctx", kb_id: "gone", top_k: "x", query_column: "c9" },
        {
          variables: ["context"],
          kbs: [{ id: "kb1", name: "Refund policy" }],
          columns: [{ id: "c1", name: "customer_message" }],
        },
      ),
    ).toEqual([
      "{{ctx}} is not in the template.",
      "That knowledge base no longer exists. Pick another one.",
      "Top must be a whole number of 1 or more.",
      "The query column is not in this dataset.",
    ]);
    expect(contextSourceIssues({})).toEqual([
      "Pick the variable to retrieve.",
      "Pick a knowledge base.",
      "Top must be a whole number of 1 or more.",
      "Pick the column the query comes from.",
    ]);
  });

  it("sends each chunk on its own line and summarizes the source", () => {
    expect(retrievedText([{ text: "A." }, { text: " " }, { text: "B." }])).toBe(
      "A.\nB.",
    );
    expect(
      contextSourceSummary(
        { variable: "context", kb_id: "kb1", top_k: 3, query_column: "c1" },
        { kbName: "Refund policy", queryName: "customer_message" },
      ),
    ).toBe("{{context}} ← Refund policy · top 3 · query from customer_message");
    expect(contextSourceSummary({})).toBeNull();
  });
});
