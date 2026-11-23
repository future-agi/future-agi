import { describe, expect, it } from "vitest";
import {
  brokeOutsideMustPassOf,
  epochEntriesOf,
  inSet,
  setCountsOf,
  sortedGroupsOf,
  tableRowsOf,
  trialsLine,
} from "../epochResultRows";

const result = {
  heldTasks: [
    { id: "h1", title: "Held one" },
    { id: "h2", title: "Held two", critical: true },
  ],
  heldPerScenario: { h1: "fixed", h2: "broke" },
  /* The must-pass check: h2 failed again on its re-run. */
  heldBlockers: [{ id: "h2", title: "Held two", critical: true }],
  trainMeasuredTasks: [
    { id: "t1", title: "T one" },
    { id: "t2", title: "T two" },
    { id: "t3", title: "T three" },
  ],
  winner: { n: 2, perScenario: { t1: "still-failing", t2: "off-script" } },
  mustPassTasks: [
    { id: "m1", title: "Must one", critical: true },
    { id: "h1", title: "dupe" },
  ],
};

describe("epoch per-scenario rows", () => {
  it("puts each scenario in one set with its result", () => {
    const e = epochEntriesOf(result);
    expect(e.map((x) => `${x.id}:${x.set}:${x.outcome}`)).toEqual([
      "h1:held:fixed",
      "h2:held:broke",
      "t1:target:still-failing",
      "t2:target:off-script",
      "t3:target:same",
      "m1:mustpass:same",
    ]);
  });

  it("reads a must-pass scenario the way the verdict's must-pass check does", () => {
    const e = epochEntriesOf({
      trainMeasuredTasks: [
        { id: "a", title: "Marked", critical: true },
        { id: "b", title: "Optional" },
      ],
      winner: { n: 1, perScenario: { a: "broke", b: "broke" } },
      mustPassTasks: [{ id: "m", title: "Protected" }],
      heldBlockers: [{ id: "m", title: "Protected" }],
    });
    /* Broke once, passed its re-run: not a break. Optional: still Broke. A
       blocker the check counted: Broke, even with no search outcome. */
    expect(
      e.map((x) => `${x.id}:${x.outcome}${x.recheck ? ":recheck" : ""}`),
    ).toEqual(["a:same:recheck", "b:broke", "m:broke"]);
    expect(
      brokeOutsideMustPassOf({
        trainMeasuredTasks: [{ id: "a", critical: true }, { id: "b" }],
        winner: { perScenario: { a: "broke", b: "broke" } },
      }).map((x) => x.id),
    ).toEqual(["b"]);
  });

  it("counts sets from the data; must-pass includes marked scenarios elsewhere", () => {
    const e = epochEntriesOf(result);
    expect(setCountsOf(e)).toEqual({ target: 3, held: 2, mustpass: 2 });
    expect(e.filter((x) => inSet(x, "all")).length).toBe(6);
  });

  it("sorts Broke, Fixed, Still failing, Unchanged, then the rest — and reverses", () => {
    const rows = tableRowsOf(epochEntriesOf(result), []);
    const [g] = sortedGroupsOf(rows, { groupBy: "none" });
    expect(g.rows.map((r) => r.epoch.outcome)).toEqual([
      "broke",
      "fixed",
      "still-failing",
      "same",
      "same",
      "off-script",
    ]);
    const [rev] = sortedGroupsOf(rows, { groupBy: "none", dir: "desc" });
    expect(rev.rows[0].epoch.outcome).toBe("off-script");
  });

  it("says trial counts plainly", () => {
    expect(trialsLine({ status: "flaky", passes: 2, repeats: 3 })).toBe(
      "2 of 3 trials passed",
    );
    expect(trialsLine({ status: "passed", repeats: 1, passShare: 1 })).toBe(
      "1 of 1 trial passed",
    );
  });
});
