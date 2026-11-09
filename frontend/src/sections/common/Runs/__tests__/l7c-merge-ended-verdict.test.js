// L7c merge (banner:verdict, g4 skeptic item 14): a COMPLETED sweep whose
// missing tries were skipped (no expected value) never reads `Partial ·
// verdict when done`; a running candidate still does.
import { describe, expect, it } from "vitest";
import { compareRuns } from "../runNumbers";
import { scopeVerdictInputs } from "src/sections/experiment-detail/matrix/verdictScope";

const matrix = {
  rows: [
    { id: "r1", n: 1, cells: { a: { trials: [1] }, b: { trials: [1] } } },
    { id: "r2", n: 2, cells: { a: { trials: [0] }, b: { trials: [1] } } },
    // A skipped try (no expected value) is null in the matrix.
    { id: "r3", n: 3, cells: { a: { trials: [1] }, b: { trials: [null] } } },
  ],
};

describe("an ended candidate has no try still to score", () => {
  it("compareRuns keeps partial while the candidate runs", () => {
    const c = compareRuns({ matrix, baselineId: "a", candidateId: "b" });
    expect(c.partial).toEqual({ scored: 2, total: 3 });
  });
  it("compareRuns drops partial once the candidate has ended", () => {
    const c = compareRuns({
      matrix,
      baselineId: "a",
      candidateId: "b",
      candidateEnded: true,
    });
    expect(c.partial).toBeNull();
    expect(c.better).toBe(1);
  });
  it("the headline reads the item's ended flag", () => {
    const item = (ended) => ({
      runId: "b",
      label: "#1B",
      ended,
      input: { metrics: { success: {} } },
    });
    const [running] = scopeVerdictInputs([item(false)], {
      matrix,
      baselineId: "a",
    });
    const [ended] = scopeVerdictInputs([item(true)], {
      matrix,
      baselineId: "a",
    });
    expect(running.compare.partial).not.toBeNull();
    expect(ended.compare.partial).toBeNull();
  });
});

describe("a stopped run's unplayed rows (status:ledger)", () => {
  it("read 'not run · stopped', never queued; a running run's stay queued", async () => {
    const { ledgerOf, runLedgerLine } = await import("../runLedger");
    const ledger = ledgerOf({
      records: [1, 2, 3].map((n) => ({
        example_id: `r${n}`,
        visibility: "queued",
        status: "ok",
        scores: {},
      })),
      primaryKey: "eval:correctness",
    });
    expect(runLedgerLine({ status: "Running", ledger })).toBe("3 rows queued");
    expect(runLedgerLine({ status: "Cancelled", ledger })).toBe(
      "3 rows not run · stopped",
    );
  });
});
