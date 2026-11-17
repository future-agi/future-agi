// integration review 2 (DI-06 d): which picked rows an expected update would
// change — the expected as it stands now (the latest version) against the
// picked try's output. The Add to dataset dialog now only copies rows into
// another or a new dataset; the pure plan stays covered here.
import { describe, expect, it } from "vitest";
import { expectedPlanOf, latestExpectedOf } from "../addToDatasetModel";

const tries = (n) => ({
  output: `Try one of row ${n}`,
  trial_outputs: [1, 2, 3].map(
    (t) => `Try ${["one", "two", "three"][t - 1]} of row ${n}`,
  ),
});
const row = (id, n, expectedNow, latest = null) => ({
  example_id: id,
  row_order: n,
  row_label: `Row ${n}`,
  expected: {
    value: { expected_reply: expectedNow },
    primary: "expected_reply",
    latest: latest ? { expected_reply: latest } : null,
  },
  results: { r4a: tries(n) },
});
// Row 18: the baseline expected differs from try 2. Row 20: it already
// equals try 2 (the save leaves it as is).
const firstRows = [
  row("e1", 18, "Old expected 18"),
  row("e2", 20, "Try two of row 20"),
];
// After that save: Row 18's latest expected is try 2 (`changed in v7`).
const savedRows = [
  row("e1", 18, "Old expected 18", "Try two of row 18"),
  row("e2", 20, "Try two of row 20"),
];

describe("expectedPlanOf (DI-06 d review 2)", () => {
  it("reads the latest expected, not the baseline's", () => {
    expect(latestExpectedOf(savedRows[0])).toBe("Try two of row 18");
    expect(latestExpectedOf(firstRows[0])).toBe("Old expected 18");
    expect(latestExpectedOf({ expected: "plain text" })).toBe("plain text");
  });

  it("splits the picked rows into change / same / none / unsure", () => {
    const first = expectedPlanOf({
      rows: firstRows,
      exampleIds: ["e1", "e2"],
      runId: "r4a",
      trial: 2,
    });
    expect(first.change.map((r) => r.example_id)).toEqual(["e1"]);
    expect(first.same.map((r) => r.example_id)).toEqual(["e2"]);
    expect(first.upTo).toBe(1);
    const again = expectedPlanOf({
      rows: savedRows,
      exampleIds: ["e1", "e2"],
      runId: "r4a",
      trial: 2,
    });
    expect(again.upTo).toBe(0);
    expect(again.same).toHaveLength(2);
    // A try the row does not have is left as is.
    const noTry = expectedPlanOf({
      rows: [{ ...firstRows[0], results: { r4a: { output: "only one" } } }],
      exampleIds: ["e1"],
      runId: "r4a",
      trial: 3,
    });
    expect(noTry.none).toHaveLength(1);
    expect(noTry.upTo).toBe(0);
  });

  it("a cut grid output that the expected starts with, or a row not on the page, is unsure", () => {
    const cut = {
      ...firstRows[0],
      expected: {
        value: { expected_reply: "Long reply that goes on" },
        primary: "expected_reply",
      },
      results: { r4a: { output: "Long reply…" } },
    };
    const plan = expectedPlanOf({
      rows: [cut],
      exampleIds: ["e1", "e9"],
      runId: "r4a",
      trial: 1,
    });
    expect(plan.unsure.map((r) => r.example_id)).toEqual(["e1", "e9"]);
    expect(plan.change).toHaveLength(0);
    expect(plan.upTo).toBe(2);
    // A cut output whose start differs from the expected is a sure change.
    const differs = expectedPlanOf({
      rows: [{ ...cut, results: { r4a: { output: "Other start…" } } }],
      exampleIds: ["e1"],
      runId: "r4a",
    });
    expect(differs.change).toHaveLength(1);
  });
});
