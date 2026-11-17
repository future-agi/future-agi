import { describe, it, expect } from "vitest";
import {
  aggValueOf,
  bestRunFor,
  compareSelection,
  compareUrl,
  examplesText,
  experimentUrl,
  fastestRun,
  filterRuns,
  groupOf,
  mostActiveRun,
  movementUrl,
  normalizeGroup,
  runConfigLine,
  stripRuns,
  targetMix,
  wholeExperiments,
} from "../leaderboardModel";

const CORRECTNESS = {
  key: "eval:correctness",
  name: "Correctness",
  output_type: "pass_fail",
  higher_is_better: true,
};
const TOXICITY = {
  key: "eval:toxicity",
  output_type: "score",
  higher_is_better: false,
};

const run = (label, extra = {}) => {
  const [, ord, letter] = /#(\d+)([A-Z])/.exec(label);
  return {
    run_id: `R${ord}${letter}`,
    label,
    experiment_id: `E${ord}`,
    experiment_ordinal: Number(ord),
    letter,
    status: "Completed",
    target_type: "prompt",
    target: {
      prompt_name: "Support Reply",
      prompt_version_name: "V5",
      model: "gpt-4o-mini",
      model_params: { temperature: 0.2 },
    },
    dataset_version: { number: 6, tag: null },
    created_at: `2026-10-0${ord}T09:00:00Z`,
    metrics: { aggregates: {} },
    ...extra,
  };
};

const withAgg = (r, key, agg, extra = {}) => ({
  ...r,
  metrics: {
    ...r.metrics,
    ...extra,
    aggregates: { ...r.metrics.aggregates, [key]: agg },
  },
});

describe("config lines and groups", () => {
  it("names what each target ran", () => {
    expect(runConfigLine(run("#7A"))).toBe(
      "Support Reply V5 · gpt-4o-mini · t=0.2",
    );
    expect(
      runConfigLine(
        run("#4A", {
          target_type: "agent",
          target: { graph_name: "Support Agent", version_name: "v3" },
        }),
      ),
    ).toBe("Support Agent v3");
    expect(
      runConfigLine(
        run("#5A", {
          target_type: "external",
          target: { branch: "main", commit: "a1f3c9e" },
        }),
      ),
    ).toBe("CI · main@a1f3c9e");
    expect(
      runConfigLine(
        run("#8A", {
          target_type: "http",
          target: {
            url: "https://api.acme.ai/v1/support/reply",
            method: "POST",
          },
        }),
      ),
    ).toBe("POST api.acme.ai/v1/support/reply");
  });

  it("groups by model, version and experiment", () => {
    const r = run("#2B", { target: { model: "claude-sonnet-4-5" } });
    expect(groupOf(r, "model")).toEqual({
      key: "model:claude-sonnet-4-5",
      label: "claude-sonnet-4-5",
    });
    expect(groupOf(run("#1A"), "dataset_version").label).toBe("v6");
    expect(groupOf(run("#1A"), "experiment").key).toBe("E1");
    expect(normalizeGroup("bogus")).toBe("experiment");
  });

  it("counts target types", () => {
    expect(
      targetMix([run("#4A"), run("#4B"), run("#4C", { target_type: "agent" })]),
    ).toBe("Prompt ×2 · Agent ×1");
    expect(targetMix([run("#1A")])).toBe("Prompt");
  });

  it("shows split coverage", () => {
    expect(examplesText({ examples: 248 })).toBe("248");
    expect(
      examplesText({
        examples: 48,
        version_rows: 248,
        splits: [{ name: "hard" }],
      }),
    ).toBe("48 of 248");
  });
});

describe("values and group summaries", () => {
  const a = withAgg(run("#2A"), CORRECTNESS.key, { avg: 0.771 });
  const b = withAgg(run("#2B"), CORRECTNESS.key, { avg: 0.866 });

  it("picks the best run by direction", () => {
    expect(bestRunFor([a, b], CORRECTNESS).label).toBe("#2B");
    const lo = withAgg(run("#3A"), TOXICITY.key, { avg: 0.05, median: 0 });
    const hi = withAgg(run("#3B"), TOXICITY.key, { avg: 0.2, median: 0.1 });
    expect(bestRunFor([lo, hi], TOXICITY).label).toBe("#3A");
    expect(aggValueOf(hi, TOXICITY, "median")).toBe(0.1);
    expect(bestRunFor([a], { ...CORRECTNESS, higher_is_better: null })).toBe(
      null,
    );
  });

  it("picks the fastest run and the most active state", () => {
    const fast = { ...run("#2A"), metrics: { latency_p50_s: 1.4 } };
    const slow = { ...run("#2B"), metrics: { latency_p50_s: 1.9 } };
    expect(fastestRun([slow, fast]).label).toBe("#2A");
    expect(
      mostActiveRun([
        run("#7A"),
        run("#7B", { status: "Running" }),
        run("#7C", { status: "Cancelled" }),
      ]).label,
    ).toBe("#7B");
  });

  it("orders the strip oldest to newest and keeps the last N", () => {
    const runs = [run("#3A"), run("#1A"), run("#2B"), run("#2A")];
    expect(stripRuns(runs).map((r) => r.label)).toEqual([
      "#1A",
      "#2A",
      "#2B",
      "#3A",
    ]);
    expect(stripRuns(runs, 2).map((r) => r.label)).toEqual(["#2B", "#3A"]);
  });
});

describe("filters", () => {
  const defs = { [CORRECTNESS.key]: CORRECTNESS };
  const runs = [
    withAgg(run("#1A"), CORRECTNESS.key, { avg: 0.794 }),
    withAgg(
      run("#4A", { target_type: "agent", status: "Completed" }),
      CORRECTNESS.key,
      { avg: 0.821 },
      { error_count: 2 },
    ),
    run("#7A", { status: "Running" }),
  ];
  const f = (column_id, filter_op, filter_value) => ({
    column_id,
    filter_config: { filter_op, filter_value },
  });

  it("applies option, number and eval filters", () => {
    const labels = (xs) => xs.map((r) => r.label);
    expect(
      labels(filterRuns(runs, [f("status", "equals", "Running")])),
    ).toEqual(["#7A"]);
    expect(labels(filterRuns(runs, [f("target", "equals", "agent")]))).toEqual([
      "#4A",
    ]);
    expect(
      labels(filterRuns(runs, [f("has_errors", "equals", "yes")])),
    ).toEqual(["#4A"]);
    expect(
      labels(
        filterRuns(
          runs,
          [f("metric:eval:correctness", "greater_than", ["80"])],
          defs,
        ),
      ),
    ).toEqual(["#4A"]);
  });

  it("ignores incomplete filter rows", () => {
    expect(filterRuns(runs, [f("status", "equals", "")])).toHaveLength(3);
    expect(
      filterRuns(runs, [{ column_id: "", filter_config: {} }]),
    ).toHaveLength(3);
  });
});

describe("selection", () => {
  const r1 = run("#1A");
  const r3 = run("#3A");
  const r4 = run("#4A");

  it("puts the view baseline first when it is selected", () => {
    expect(compareSelection([r4, r1], "R1A")).toEqual({
      runIds: ["R1A", "R4A"],
      baselineId: "R1A",
      candId: "R4A",
      error: null,
      runs: [r4, r1],
    });
  });

  it("uses the oldest selected run otherwise", () => {
    expect(compareSelection([r4, r3], "R1A")).toEqual({
      runIds: ["R3A", "R4A"],
      baselineId: "R3A",
      candId: "R4A",
      error: null,
      runs: [r4, r3],
    });
  });

  it("opens Matrix for runs of different kinds (R2 §0.3)", () => {
    const http = { ...run("#8A"), run_id: "R8A", target_type: "http" };
    const sel = compareSelection([r4, http], null);
    expect(compareUrl("DS", sel)).toMatch(/\/compare\/matrix\?/);
    expect(compareUrl("DS", compareSelection([r4, r3], null))).toMatch(
      /\/compare\/data\?/,
    );
  });

  it("compares one run with the view baseline", () => {
    expect(compareSelection([r4], "R1A").runIds).toEqual(["R1A", "R4A"]);
    expect(compareSelection([r1], "R1A").error).toMatch(/baseline run/);
  });

  it("caps at 8 runs", () => {
    const many = Array.from({ length: 9 }, (_, i) => run(`#${i + 1}A`));
    expect(compareSelection(many, null).error).toMatch(
      /^Compare takes 2-8 runs · you selected \d+$/,
    );
  });

  it("deletes whole experiments only", () => {
    const all = [run("#2A"), run("#2B"), run("#1A")];
    expect(wholeExperiments([all[0], all[2]], all)).toEqual({
      experimentIds: ["E1"],
      partial: true,
    });
    expect(wholeExperiments(all, all)).toEqual({
      experimentIds: ["E2", "E1"],
      partial: false,
    });
  });
});

describe("links", () => {
  it("builds the J5 movement link with a movement pill", () => {
    const url = movementUrl("DS", {
      baselineId: "R1A",
      runId: "R2B",
      metricKey: "eval:correctness",
      dir: "regressed",
    });
    expect(
      url.startsWith(
        "/dashboard/develop/DS/compare/data?runs=R1A,R2B&baseline=R1A&filters=",
      ),
    ).toBe(true);
    const filters = new URL(url, "http://x").searchParams.get("filters");
    expect(JSON.parse(filters)).toEqual([
      {
        type: "movement",
        metric: "eval:correctness",
        dir: "regressed",
        run: "R2B",
      },
    ]);
    expect(url).not.toContain("show=");
  });

  it("adds review and return params", () => {
    expect(
      compareUrl("DS", {
        runIds: ["R3A", "R4A"],
        baselineId: "R3A",
        review: "pairwise",
        from: "/dashboard/develop/DS?tab=experiments",
      }),
    ).toBe(
      "/dashboard/develop/DS/compare/data?runs=R3A,R4A&baseline=R3A&review=pairwise&from=%2Fdashboard%2Fdevelop%2FDS%3Ftab%3Dexperiments",
    );
  });

  it("opens the experiment on a run with an error pill", () => {
    const url = experimentUrl("DS", "E4", {
      runId: "R4A",
      pills: [{ type: "error", run: "R4A" }],
    });
    expect(url).toBe(
      `/dashboard/develop/experiment/E4/data?datasetId=DS&run=R4A&filters=${encodeURIComponent('[{"type":"error","run":"R4A"}]')}`,
    );
  });
});

describe("IT-R4 G2 P4: compareSelection names baseline and candidate", () => {
  const r = (id, created) => ({ run_id: id, label: id, created_at: created });
  const A = r("#2A", "2026-10-01T00:00:01Z");
  const B = r("#2B", "2026-10-01T00:00:02Z");
  const D = r("#2D", "2026-10-01T00:00:04Z");
  const E = r("#2E", "2026-10-01T00:00:05Z");
  it("uses the pinned baseline over a view baseline", () => {
    const c = compareSelection([A, B, D], "#2A", { pinnedId: "#2B" });
    expect(c.baselineId).toBe("#2B");
    expect(c.runIds[0]).toBe("#2B");
    expect(c.candId).toBe("#2D");
  });
  it("cand = headline candidate when selected, else newest selected", () => {
    expect(
      compareSelection([A, B, D, E], "#2B", { pinnedId: "#2B", candId: "#2D" })
        .candId,
    ).toBe("#2D");
    expect(
      compareSelection([A, B, E], "#2B", { pinnedId: "#2B", candId: "#2D" })
        .candId,
    ).toBe("#2E");
  });
  it("the link carries runs, baseline and cand", () => {
    const c = compareSelection([A, B, D], null, { pinnedId: "#2B" });
    const url = compareUrl("d", c);
    expect(url).toContain("baseline=#2B");
    expect(url).toContain("cand=#2D");
  });
});
