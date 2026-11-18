import { describe, expect, it } from "vitest";

import { buildJevMapping, orderChoiceScores } from "../jevMapping";

// What the detail API returns for scores authored as Resolved, Unresolved,
// Escalated: choice_scores is jsonb, which hands keys back shortest first.
const JSONB_SCORES = { Resolved: 1, Escalated: 0.5, Unresolved: 0 };
const AUTHORED = ["Resolved", "Unresolved", "Escalated"];

const labelsOf = (choiceScores, draft) =>
  buildJevMapping("deterministic", choiceScores, draft).choice.labels;

describe("buildJevMapping choice labels", () => {
  it("keeps the authored label order when choice_scores comes back in jsonb order", () => {
    const draft = {
      choice: {
        labels: AUTHORED,
        descriptions: { Resolved: "Fixed", Unresolved: "", Escalated: "" },
      },
    };
    const mapping = buildJevMapping("deterministic", JSONB_SCORES, draft);
    expect(mapping.choice.labels).toEqual(AUTHORED);
    expect(mapping.choice.descriptions).toEqual(draft.choice.descriptions);
  });

  it("falls back to the choice_scores key order when no order was authored", () => {
    expect(labelsOf(JSONB_SCORES)).toEqual([
      "Resolved",
      "Escalated",
      "Unresolved",
    ]);
    expect(labelsOf(JSONB_SCORES, { choice: { descriptions: {} } })).toEqual([
      "Resolved",
      "Escalated",
      "Unresolved",
    ]);
  });

  it("keeps a renamed label in the slot the editor shows it in", () => {
    const edited = { Resolved: 1, Open: 0, Escalated: 0.5 };
    expect(labelsOf(edited, { choice: { labels: AUTHORED } })).toEqual([
      "Resolved",
      "Open",
      "Escalated",
    ]);
  });

  it("drops removed labels and keeps added ones where the author put them", () => {
    expect(
      labelsOf(
        { Resolved: 1, Escalated: 0.5, Unresolved: 0, Spam: 0 },
        { choice: { labels: AUTHORED } },
      ),
    ).toEqual([...AUTHORED, "Spam"]);
    expect(
      labelsOf(
        { Resolved: 1, Escalated: 0.5 },
        { choice: { labels: AUTHORED } },
      ),
    ).toEqual(["Resolved", "Escalated"]);
  });

  it("keeps the authored order of integer-like labels, which objects reorder", () => {
    // JS objects always list integer-like keys first, in ascending order, so
    // only the authored array can carry this order.
    expect(
      labelsOf(
        { 10: 1, 2: 0.5, 1: 0 },
        { choice: { labels: ["10", "2", "1"] } },
      ),
    ).toEqual(["10", "2", "1"]);
  });
});

describe("orderChoiceScores", () => {
  it("rebuilds loaded scores in the authored order and keeps every score", () => {
    const ordered = orderChoiceScores(JSONB_SCORES, AUTHORED);
    expect(Object.keys(ordered)).toEqual(AUTHORED);
    expect(ordered).toEqual(JSONB_SCORES);
  });

  it("returns the scores unchanged without an authored order", () => {
    for (const order of [undefined, null, []]) {
      expect(Object.keys(orderChoiceScores(JSONB_SCORES, order))).toEqual([
        "Resolved",
        "Escalated",
        "Unresolved",
      ]);
    }
    expect(orderChoiceScores(undefined, AUTHORED)).toEqual({});
  });

  it("ignores authored labels that are no longer scored", () => {
    expect(
      Object.keys(
        orderChoiceScores({ Escalated: 0.5, Resolved: 1 }, [
          "Resolved",
          "Gone",
          "Escalated",
        ]),
      ),
    ).toEqual(["Resolved", "Escalated"]);
  });
});
