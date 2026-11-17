// How a row's tries become one value when some tries errored, hit an
// infrastructure failure, are still running or were not scored. The Results
// read's per-try aggregates read tries the same way:
// - pass@k, pass^k and at least n count an errored or infra try as a failed
//   try whatever the pass mark, 0 included (a try that did not pass did not
//   pass);
// - Most common and Best read the scored tries only;
// - an unscored or unfinished try is left out of every reducer;
// - pass^k reads each row against its own planned tries.
import { describe, expect, it } from "vitest";
import { reduceRow, reduceRun, verdictsAt } from "../trialReducers";

const row = (tries, kind, extra = {}) => reduceRow(tries, { kind, ...extra });

describe("pass@k, pass^k and at least n", () => {
  it("count an errored or infra try as a failed try", () => {
    expect(row(["infra", "infra"], "pass_at_k", { k: 2 })).toBe(0);
    expect(row([1, "infra", 1], "pass_hat_k", { k: 3 })).toBe(0);
    expect(row(["infra"], "at_least", { n: 1 })).toBe(0);
    expect(row([1, "infra", 1], "at_least", { n: 2 })).toBe(1);
    expect(row(["error", "error"], "pass_at_k", { k: 2 })).toBe(0);
    expect(row([1, "error", 1], "pass_hat_k", { k: 3 })).toBe(0);
    expect(row([1, "error", 0], "at_least", { n: 2 })).toBe(0);
  });

  it("leave out a try that is unscored or still running, and count the rest", () => {
    expect(row([1, null, "running"], "pass_hat_k", { k: 3 })).toBeNull();
    expect(row([null, "running"], "pass_at_k", { k: 2 })).toBeNull();
    expect(row([0, null, 1], "pass_at_k", { k: 3 })).toBe(1);
    // Three tries count: the infra try is one of them.
    expect(row([1, "infra", 1, null], "pass_hat_k", { k: 3 })).toBe(0);
  });
});

describe("Most common and Best", () => {
  it("read the scored tries only", () => {
    expect(row([1, "error", "error"], "mode")).toBe(1);
    expect(row([1, "infra", "error"], "mode")).toBe(1);
    // A tie among the scored tries is still a fail.
    expect(row([1, 0, "error"], "mode")).toBe(0);
    expect(row([0.4, "error"], "max")).toBe(0.4);
  });

  it("have no value for a row with no scored try", () => {
    expect(row(["error", "infra"], "max")).toBeNull();
    expect(row(["error", "error"], "mode")).toBeNull();
  });

  it("read a score's Most common from the verdicts of its scored tries", () => {
    const r = reduceRun([[0.9, "error", "error"]], {
      kind: "mode",
      passFail: false,
      threshold: 0.5,
    });
    expect(r.value).toBe(1);
    expect(r.n).toBe(1);
  });
});

describe("A run's number over rows with failed tries", () => {
  // A pass/fail evaluation, three tries per row.
  const rows = [
    [1, 1, 1],
    [1, 1, "error"],
    ["infra", 0, 1],
    [0, 0, 0],
  ];
  const run = (kind, extra = {}) => {
    const r = reduceRun(rows, { kind, k: 3, ...extra });
    return [r.value, r.n];
  };

  it("matches the read's per-try aggregates of the same tries", () => {
    expect(run("mode")).toEqual([2 / 4, 4]);
    expect(run("max")).toEqual([3 / 4, 4]);
    expect(run("pass_at_k")).toEqual([3 / 4, 4]);
    expect(run("pass_hat_k")).toEqual([1 / 4, 4]);
    expect(run("at_least", { n: 2 })).toEqual([2 / 4, 4]);
    expect(run("at_least", { n: 3 })).toEqual([1 / 4, 4]);
  });

  it("reads each try as a verdict at the pass mark", () => {
    // An errored or infra try fails; an unscored try stays unscored.
    expect(verdictsAt([0.9, 0.2, "error", "infra", null], 0.5)).toEqual([
      1,
      0,
      0,
      0,
      null,
    ]);
  });
});

describe("A failed or infra try at a pass mark of 0", () => {
  it("still fails pass@k, pass^k and at least n", () => {
    const at0 = (tries, kind, extra = {}) =>
      row(tries, kind, { threshold: 0, ...extra });
    expect(at0(["infra"], "pass_at_k", { k: 1 })).toBe(0);
    expect(at0(["error"], "pass_at_k", { k: 1 })).toBe(0);
    expect(at0(["error", 0.2], "pass_hat_k", { k: 2 })).toBe(0);
    expect(at0(["error", "infra", 0.5], "at_least", { n: 2 })).toBe(0);
    // A value at the mark itself passes; the failed try beside it does not.
    expect(at0([0, "infra"], "pass_at_k", { k: 2 })).toBe(1);
    expect(at0([0, "infra"], "pass_hat_k", { k: 2 })).toBe(0);
    expect(at0([0, "infra"], "at_least", { n: 2 })).toBe(0);
  });

  it("reads as a failed verdict", () => {
    expect(verdictsAt(["infra"], 0)).toEqual([0]);
    expect(verdictsAt(["error", "infra", 0, null], 0)).toEqual([0, 0, 1, null]);
  });

  it("fails a run's pass@k", () => {
    const r = reduceRun(
      [
        ["infra", "error"],
        [0.1, 0.3],
      ],
      { kind: "pass_at_k", k: 2, threshold: 0, passFail: false },
    );
    expect([r.value, r.n]).toEqual([1 / 2, 2]);
  });
});

describe("Rows planned with different numbers of tries", () => {
  // One row was planned two tries and the other three, so the run plays
  // three per row. Each row is read against its own planned tries.
  const rows = [
    [1, 0],
    [1, 1, 1],
  ];
  const run = (kind, extra = {}) => {
    const r = reduceRun(rows, { kind, k: 3, ...extra });
    return [r.value, r.n];
  };

  it("read pass^k of each row against its own planned tries", () => {
    expect(run("pass_hat_k")).toEqual([1 / 2, 2]);
    // A row whose own two tries both pass passes every try.
    expect(row([1, 1], "pass_hat_k", { k: 3 })).toBe(1);
    // One of its own tries did not count: left out.
    expect(row([1, null], "pass_hat_k", { k: 3 })).toBeNull();
  });

  it("match the read's per-try aggregates of the same tries", () => {
    expect(run("mode")).toEqual([1 / 2, 2]);
    expect(run("max")).toEqual([1, 2]);
    expect(run("pass_at_k")).toEqual([1, 2]);
    expect(run("pass_hat_k")).toEqual([1 / 2, 2]);
    expect(run("at_least", { n: 2 })).toEqual([1 / 2, 2]);
    expect(run("at_least", { n: 3 })).toEqual([1 / 2, 2]);
  });
});
