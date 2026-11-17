// v2.1 g4 (banner:unscored-link, banner:verdict): tries whose primary eval
// was skipped never get a score; once they are the only tries without one,
// the rerun comparison is settled (no `Partial · …`), so the verdict and
// the unscored line render on a finished run.
import { describe, expect, it } from "vitest";
import { settleUnscoredPartial, unscoredOf } from "../unscoredModel";

const C = "cand";
const KEY = "eval:correctness";
const matrix = (cells) => ({
  rows: cells.map((trials, i) => ({
    row_id: `r${i}`,
    cells: { base: { trials: [1] }, [C]: { trials } },
  })),
});
const results = (statuses) =>
  statuses.map((st, i) => ({
    example_id: `r${i}`,
    results: {
      [C]: {
        scores: {
          [KEY]:
            st === "skipped"
              ? {
                  value: null,
                  status: "skipped",
                  skip_reason: "no expected value",
                }
              : st === "pending"
                ? { value: null, status: "running" }
                : { value: 1, status: "scored" },
        },
      },
    },
  }));

describe("settleUnscoredPartial", () => {
  it("settles when every missing try is on a row whose primary eval was skipped", () => {
    const m = matrix([[1], [0], [null], [null]]);
    const cmp = { partial: { scored: 2, total: 4 } };
    const out = settleUnscoredPartial(
      cmp,
      m,
      results(["ok", "ok", "skipped", "skipped"]),
      C,
      KEY,
    );
    expect(out.partial).toBeNull();
    const line = unscoredOf(results(["ok", "ok", "skipped", "skipped"]), C);
    expect(line.text).toBe(
      "2 rows not scored · eval:correctness skipped (no expected value)",
    );
    expect(line.linkIds).toEqual(["r2", "r3"]);
  });

  it("stays partial while a try on a scored row is still running", () => {
    const m = matrix([[1], [null], [null]]);
    const cmp = { partial: { scored: 1, total: 3 } };
    const out = settleUnscoredPartial(
      cmp,
      m,
      results(["ok", "pending", "skipped"]),
      C,
      KEY,
    );
    expect(out).toBe(cmp);
  });

  it("stays partial while tries the run expects have not arrived", () => {
    const m = matrix([[1], [null]]);
    // 3 tries expected per row: the second row has only one so far.
    const cmp = { partial: { scored: 1, total: 6 } };
    expect(
      settleUnscoredPartial(cmp, m, results(["ok", "skipped"]), C, KEY),
    ).toBe(cmp);
  });

  it("no partial, no primary key: unchanged", () => {
    const cmp = { partial: null };
    expect(settleUnscoredPartial(cmp, matrix([[1]]), [], C, KEY)).toBe(cmp);
    const p = { partial: { scored: 0, total: 1 } };
    expect(settleUnscoredPartial(p, matrix([[null]]), [], C, null)).toBe(p);
  });
});
