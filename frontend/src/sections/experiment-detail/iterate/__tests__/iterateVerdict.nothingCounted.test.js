// Test a fix when one of the two runs has nothing to count (no evaluation
// with a Pass/fail result): no row is compared, so none is better, worse,
// same, fixed, still failing or a new failure; the verdict, the sign test and
// the finished toast say so; and the pass@1 chip keeps its click with `—`.
import { describe, expect, it } from "vitest";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import * as summary from "../iterateSummary";
import * as store from "../iterateStore";
import { fixVerdict, fullRunVerdict } from "../fixVerdict";
import bannerSource from "../IterateResultBanner.jsx?raw";

const NONE = "run-none";
const PASSES = "run-passes";

// Two rows of two tries. NONE errored on every try; PASSES passed every try.
const matrixOf = (columns) => ({
  rows: [1, 2].map((n) => ({
    row_id: `row-${n}`,
    n,
    cells: {
      [NONE]: { trials: ["error", "error"] },
      [PASSES]: { trials: [1, 1] },
    },
  })),
  ...(columns ? { columns } : {}),
});
const COUNTS = {
  [NONE]: { trials: 2, pass_fail_evals: 0 },
  [PASSES]: { trials: 2, pass_fail_evals: 1 },
};
const NOT_COMPARED = {
  better: [],
  worse: [],
  same: [],
  fixed: [],
  stillFailing: [],
  newFailures: [],
};

describe("a fix run whose base has nothing to count", () => {
  const s = summary.summarizeIteration(matrixOf(COUNTS), NONE, PASSES);

  it("compares no row", () => {
    expect(s).toMatchObject({ ...NOT_COMPARED, noPassFail: true });
    expect(s.rerun).toEqual(["row-1", "row-2"]);
  });

  it("has nothing for the sign test, and says why", () => {
    expect(summary.signSentence(s)).toBe(
      `No row is compared, so there is nothing to test. ${SUCCESS_NEEDS_PASS_FAIL}`,
    );
  });

  it("gives a rerun no verdict, and says why", () => {
    const v = fixVerdict(s, { from: "#1" });
    expect(v.word).toBe("No verdict");
    expect(v.text).toBe(
      `No verdict: none of the 2 rows rerun from #1 is compared · ${SUCCESS_NEEDS_PASS_FAIL}`,
    );
  });

  it("gives a full run no verdict, and says why", () => {
    const v = fullRunVerdict(
      { better: 0, worse: 0, same: 0, notRun: 2, worseIds: [] },
      { name: "v3", total: 2, noPassFail: s.noPassFail },
    );
    expect(v.word).toBe("No verdict");
    expect(v.text).toBe(
      `Full run of v3 on 2 rows: no verdict · ${SUCCESS_NEEDS_PASS_FAIL}`,
    );
  });

  it("says no verdict in the finished toast", () => {
    expect(store.finishedSummaryText?.(s)).toBe("no verdict");
  });

  it("keeps the pass@1 chip with — for it", () => {
    expect(summary.pass1Move?.(s)).toBe("— → 100%");
  });
});

describe("a fix run that has nothing to count itself", () => {
  const s = summary.summarizeIteration(matrixOf(COUNTS), PASSES, NONE);

  it("counts none of its errored rows as worse or as a new failure", () => {
    expect(s).toMatchObject({ ...NOT_COMPARED, noPassFail: true });
  });

  it("keeps the pass@1 chip with — for it", () => {
    expect(summary.pass1Move?.(s)).toBe("100% → —");
  });
});

describe("the banner reads these", () => {
  it("keeps the pass@1 chip, gives the full run's flag and drops p", () => {
    // Reads the source: a wiring pin.
    expect(bannerSource).toMatch(
      /successMove\(cmpScored\) \?\? pass1Move\(s\)/,
    );
    expect(bannerSource).toMatch(/noPassFail: s\.noPassFail/);
    expect(bannerSource).toMatch(/s\.noPassFail \? TEST_NAME :/);
  });
});

describe("two runs with figures", () => {
  it("are compared as before", () => {
    // Green at the base.
    const cols = { ...COUNTS, [NONE]: { trials: 2, pass_fail_evals: 1 } };
    const s = summary.summarizeIteration(matrixOf(cols), NONE, PASSES);
    expect(s.better).toEqual(["row-1", "row-2"]);
    expect(summary.signSentence(s)).toMatch(/^2 rows got better and none/);
  });
});
