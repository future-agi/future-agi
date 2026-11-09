import { describe, expect, it } from "vitest";
import { bannerPairOf } from "../bannerPair";
import { backOf, entryFromSearch, withoutOneShot } from "../useIterateParams";
import { causeCheckLabel, causeOf, initialDraft } from "../useIterateDock";
import { headerCandidate } from "../../results/pageCandidate";

describe("bannerPairOf (r2 P2)", () => {
  const last = {
    runId: "F",
    baseId: "B",
    datasetId: "d",
    startedPath: "/dashboard/develop/experiment/e/data",
  };
  it("the finished fix-run on the page it started from", () => {
    expect(
      bannerPairOf({
        pathname: "/dashboard/develop/experiment/e/matrix",
        search: "",
        datasetId: "d",
        runIds: ["B", "D"],
        baselineId: "B",
        last,
      }),
    ).toMatchObject({ baseId: "B", candId: "F", fromPage: false });
  });
  it("from=iterate pairs the page's runs; dismissed and Summary hide it", () => {
    const args = {
      pathname: "/dashboard/develop/d/compare/data",
      search: "?from=iterate",
      datasetId: "d",
      runIds: ["B", "G"],
      baselineId: "B",
    };
    expect(bannerPairOf(args)).toMatchObject({ baseId: "B", candId: "G" });
    expect(bannerPairOf({ ...args, dismissed: ["G"] })).toBeNull();
    expect(
      bannerPairOf({
        ...args,
        pathname: "/dashboard/develop/d/compare/summary",
      }),
    ).toBeNull();
  });
});

describe("headerCandidate (r2 P2, rule 12)", () => {
  const runIds = ["B", "D", "F", "G"];
  it("experiment: cand= › run= › the page candidate", () => {
    const base = { mode: "experiment", baselineId: "B", runIds };
    expect(headerCandidate({ ...base, pageCand: "G" })).toBe("G");
    expect(headerCandidate({ ...base, cand: "D", pageCand: "G" })).toBe("D");
    expect(headerCandidate({ ...base, run: "F", pageCand: "G" })).toBe("F");
  });
  it("compare: the page candidate wins over run=", () => {
    expect(
      headerCandidate({
        mode: "compare",
        run: "F",
        pageCand: "G",
        baselineId: "B",
        runIds,
      }),
    ).toBe("G");
  });
});

describe("Fix this step door (r2 P3, S1)", () => {
  it("reads back= and drops it with the other one-shot params", () => {
    const back = encodeURIComponent("/dashboard/develop/d/compare/tasks?row=x");
    const search = `?iterate=B&scope=rows&rows=r1,r2&cause=RC_PREFIX&cand=G&baseline=B&back=${back}&iterate_from=failed_lane&node=classify_intent`;
    const e = entryFromSearch(search);
    expect(e).toMatchObject({
      runId: "B",
      scope: "rows",
      rows: ["r1", "r2"],
      cause: "RC_PREFIX",
      back: "/dashboard/develop/d/compare/tasks?row=x",
    });
    expect(withoutOneShot(search)).toBe("?cand=G&baseline=B");
    expect(backOf("https://evil.example/x")).toBeNull();
    expect(backOf("//evil.example")).toBeNull();
  });
  it("a Failed door keeps no `1 selected row`; a rows door keeps its rows", () => {
    const parent = { run_id: "B", config: { trials: 3 } };
    expect(
      initialDraft(parent, { runId: "B", scope: "failed", rows: ["T26"] }).rows,
    ).toEqual([]);
    expect(
      initialDraft(parent, { runId: "B", scope: "rows", rows: ["T26"] }).rows,
    ).toEqual(["T26"]);
  });
  it("the finding on the dock's run and its check name", () => {
    const causes = [
      { id: "RC_PREFIX", run_id: "A", failed_eval: "check:order_found" },
      { id: "RC_PREFIX", run_id: "B", failed_eval: "check:order_found" },
    ];
    expect(causeOf(causes, "RC_PREFIX", "B").run_id).toBe("B");
    expect(causeCheckLabel(causes[0])).toBe("order_found");
    expect(causeOf(causes, null, "B")).toBeNull();
  });
});
