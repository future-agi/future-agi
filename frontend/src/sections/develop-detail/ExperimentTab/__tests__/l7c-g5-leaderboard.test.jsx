// L7c g5 (experiment leaderboard): kept-run search, nesting words, cost per
// task, the errors column, the filter kinds, ranking while scoring and the
// status chip without a Retry button.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { searchRuns, nestWordsOfRoot, withKeptCounts } from "../attemptsModel";
import { costPerTask } from "../leaderboardHeadline";
import { errorsOf, isRunFinal } from "../leaderboardModel";
import { leaderboardFilterDefinition } from "../leaderboardFilterDefinition";
import { columnCatalog } from "../leaderboardColumns";
import { runMatches } from "src/sections/common/Runs/runMatch";
import RunStatusChip from "src/sections/common/Runs/RunStatusChip";

const run = (label, extra = {}) => ({
  run_id: label,
  label,
  experiment_id: "e2",
  letter: label.replace(/^#\d+/, "").replace(/·\d+$/, ""),
  ...extra,
});

describe("kept:label-search", () => {
  const runs = [
    run("#2A", { latest_of_config: false }),
    run("#2A·2", { latest_of_config: false, attempt: 2 }),
    run("#2A·3", { attempt: 3 }),
    run("#2B"),
  ];
  it("a dotted label finds a kept run at any position", () => {
    for (const q of ["#2A·2", "#2A.2", "2a-2", "#2A 2"])
      expect(searchRuns(runs, "", q, runMatches).map((r) => r.label)).toEqual([
        "#2A·2",
      ]);
    expect(
      searchRuns(runs, "", "#2A·3", runMatches).map((r) => r.label),
    ).toEqual(["#2A·3"]);
    expect(searchRuns(runs, "", "", runMatches).map((r) => r.label)).toEqual([
      "#2A·3",
      "#2B",
    ]);
  });
});

describe("kept:nesting-words", () => {
  it("kept runs are never called iterations", () => {
    const root = run("#2B");
    const mixed = nestWordsOfRoot({
      ...root,
      __iterations: 5,
      __kept: 2,
      __latest: run("#2B·3", { attempt: 3, letter: "B" }),
    });
    expect(mixed.count).toBe("3 iterations · 2 more runs");
    expect(mixed.latest).toBe("latest run");
    const iter = nestWordsOfRoot({
      ...root,
      letter: "B",
      __iterations: 5,
      __kept: 2,
      __latest: run("#2D", { letter: "D" }),
    });
    expect(iter.latest).toBe("latest iteration");
    expect(nestWordsOfRoot({ ...root, __iterations: 2, __kept: 2 }).count).toBe(
      "2 more runs",
    );
  });
  it("a kept run of an iteration (#2C·2 under #2B) is a kept run", () => {
    const root = { ...run("#2B"), letter: "B", __iterations: 3, __kept: 0 };
    const kids = [
      { ...run("#2C"), letter: "C", __depth: 1, __root: "#2B" },
      {
        ...run("#2C·2", { attempt: 2 }),
        letter: "C",
        __depth: 1,
        __root: "#2B",
      },
      {
        ...run("#2B·2", { attempt: 2 }),
        letter: "B",
        __depth: 1,
        __root: "#2B",
      },
    ];
    const [counted] = withKeptCounts([root], [root, ...kids]);
    expect(counted.__kept).toBe(2);
    const words = nestWordsOfRoot({ ...counted, __latest: kids[1] });
    expect(words.count).toBe("1 iteration · 2 more runs");
    expect(words.latest).toBe("latest run");
  });
});

describe("numbers", () => {
  it("cost per task is the unrounded total over rows done (lb:compare)", () => {
    const r = {
      metrics: { cost_usd: 0.12, aggregates: { "ops:cost": { sum: 0.1234 } } },
      progress: { done: 20 },
    };
    expect(costPerTask(r)).toBeCloseTo(0.00617, 6);
    expect(
      costPerTask({ metrics: { cost_usd: 0.12 }, progress: { done: 20 } }),
    ).toBeCloseTo(0.006, 6);
  });
  it("Errors · rows counts the ledger's errored rows (status:leaderboard-errors)", () => {
    expect(
      errorsOf({
        ledger: { rows: { errored: { timeout: 2 } } },
        metrics: { error_count: 0 },
      }),
    ).toBe(2);
    expect(errorsOf({ metrics: { error_count: 3 } })).toBe(3);
    const col = columnCatalog([], []).find((c) => c.id === "errors");
    expect(col?.label).toBe("Errors · rows");
  });
  it("a run still scoring is not final, so it is not ranked (kept:so-far-headline)", () => {
    expect(
      isRunFinal({
        status: "Completed",
        progress: { done: 56, total: 56, scored: 40 },
      }),
    ).toBe(false);
    expect(
      isRunFinal({
        status: "Completed",
        progress: { done: 56, total: 56, scored: 56 },
      }),
    ).toBe(true);
  });
});

describe("lb:tags filter kinds", () => {
  it("offers only kinds a run can start with today, and no Framework filter", () => {
    const runs = [
      { run_id: "p", target_type: "prompt", config: { kind: "prompt" } },
      { run_id: "c", target_type: "external", config: { kind: "code" } },
      { run_id: "h", config: { kind: "harness" } },
    ];
    const def = leaderboardFilterDefinition(runs, []);
    expect(def.find((d) => d.propertyId === "framework")).toBeUndefined();
    const kind = def.find((d) => d.propertyId === "kind");
    const labels = JSON.stringify(kind);
    expect(labels).not.toMatch(/Your code|Harness/);
  });
});

describe("action:rerun:status-chip-retry (hidden)", () => {
  it("the status chip offers no Retry button, even with errored rows", () => {
    const onRetry = vi.fn();
    const { container } = render(
      <RunStatusChip
        run={{
          label: "#4A",
          status: "Failed",
          progress: { done: 5, total: 5, errors: 2 },
        }}
        actions={{ onRetry, onStop: vi.fn(), onResume: vi.fn() }}
      />,
    );
    expect(
      container.querySelectorAll(".run-status-actions button"),
    ).toHaveLength(0);
    expect(container.innerHTML).not.toMatch(/Retry/);
  });
});
