import { describe, it, expect } from "vitest";
import {
  compareSummaryHref,
  datasetGridHref,
  resultsGridHref,
  reviewHref,
} from "../analysisLinks";
import { analysisCsv } from "../analysisCsv";
import { parsePills } from "src/sections/common/Results/filterPills";
import { isInAppRowLink } from "src/sections/experiment-detail/ExperimentSummary/Analysis/useFalconRowLinks";
import {
  analysisQuestions,
  buildAnalysisContext,
} from "src/sections/falcon-ai/helpers/openFalconAnalysis";

const pill = {
  type: "movement",
  metric: "eval:task_completion",
  dir: "regressed",
  run: "R4",
};

describe("analysisLinks", () => {
  it("experiment links keep the baseline and carry pill JSON", () => {
    const href = resultsGridHref({
      mode: "experiment",
      datasetId: "DS",
      experimentId: "E4",
      baselineId: "R3",
      pills: [pill],
    });
    const url = new URL(href, "http://x");
    expect(url.pathname).toBe("/dashboard/develop/experiment/E4/data");
    expect(url.searchParams.get("baseline")).toBe("R3");
    expect(parsePills(url.searchParams.get("filters"))).toEqual([pill]);
  });

  it("compare links carry runs, baseline, filters and row; none drops the baseline", () => {
    const href = resultsGridHref({
      mode: "compare",
      datasetId: "DS",
      runIds: ["R3", "R4"],
      baselineId: "none",
      pills: [],
      row: "ROW_57",
    });
    const url = new URL(href, "http://x");
    expect(url.pathname).toBe("/dashboard/develop/DS/compare/data");
    expect(url.searchParams.get("runs")).toBe("R3,R4");
    expect(url.searchParams.has("baseline")).toBe(false);
    expect(url.searchParams.has("filters")).toBe(false);
    expect(url.searchParams.get("row")).toBe("ROW_57");
  });

  it("compare summary, dataset grid and review links", () => {
    expect(
      compareSummaryHref({
        datasetId: "DS",
        runIds: ["R3", "R4"],
        baselineId: "R3",
      }),
    ).toBe("/dashboard/develop/DS/compare/summary?runs=R3%2CR4&baseline=R3");
    const filters = [
      {
        column_id: "c",
        filter_config: {
          filter_type: "number",
          filter_op: "less_than",
          filter_value: 0.7,
        },
      },
    ];
    const url = new URL(datasetGridHref("DS", filters), "http://x");
    expect(url.searchParams.get("tab")).toBe("data");
    expect(JSON.parse(url.searchParams.get("filters"))).toEqual(filters);
    expect(reviewHref("DS", "Q2", "/back")).toBe(
      "/dashboard/develop/DS/review/Q2?from=%2Fback",
    );
  });
});

describe("analysisCsv", () => {
  it("writes one line per run with metric values and deltas", () => {
    const csv = analysisCsv({
      baseline_run_id: "R3",
      runs: [
        {
          run_id: "R3",
          label: "#3A",
          name: "Agent, v2",
          status: "Completed",
          in_view: 238,
        },
        {
          run_id: "R4",
          label: "#4A",
          name: "Agent v3",
          status: "Completed",
          in_view: 246,
        },
      ],
      metrics: [
        {
          key: "eval:tc",
          per_run: [
            { run_id: "R3", value: 0.7773 },
            { run_id: "R4", value: 0.8 },
          ],
          vs_baseline: [{ run_id: "R4", delta: 0.0254 }],
        },
      ],
    }).split("\n");
    expect(lines(csv)).toBe(3);
    expect(csv[1]).toContain('"Agent, v2"');
    expect(csv[1]).toContain(",yes,");
    expect(csv[2]).toContain("0.0254");
  });
});

function lines(arr) {
  return arr.length;
}

describe("Falcon helpers", () => {
  it("builds the context block with ids, labels and the baseline", () => {
    const text = buildAnalysisContext({
      datasetId: "DS",
      runs: [
        { run_id: "R3", label: "#3A" },
        { run_id: "R4", label: "#4A" },
      ],
      baselineId: "R3",
      url: "/dashboard/develop/DS/compare/summary",
    });
    expect(text).toContain("- dataset_id: DS");
    expect(text).toContain("- runs: #3A=R3, #4A=R4");
    expect(text).toContain("- baseline: #3A=R3");
    expect(
      analysisQuestions({
        candidateLabel: "#4A",
        regressedMetricName: "Task completion",
      })[1],
    ).toBe("Why did #4A regress on Task completion?");
  });

  it("intercepts only in-app row links of this dataset", () => {
    expect(
      isInAppRowLink("/dashboard/develop/DS/compare/data?runs=a&row=R", "DS"),
    ).toBe(true);
    expect(
      isInAppRowLink("/dashboard/develop/experiment/E/data?row=R", "DS"),
    ).toBe(true);
    expect(
      isInAppRowLink("/dashboard/develop/OTHER/compare/data?row=R", "DS"),
    ).toBe(false);
    expect(isInAppRowLink("https://example.com/?row=1", "DS")).toBe(false);
    expect(isInAppRowLink("/dashboard/develop/DS?tab=data", "DS")).toBe(false);
  });
});

// ------------------------------------------------------------- Round 2
import {
  byDatasetHref,
  compareTracesHref,
  iterateHref,
  matrixHref,
  readR2Params,
  workbenchHref,
} from "../analysisLinks";

describe("R2 builders", () => {
  it("matrixHref: compare and experiment", () => {
    expect(
      matrixHref({
        mode: "compare",
        datasetId: "DS",
        runIds: ["A", "B"],
        baselineId: "A",
        group: "category",
        show: "disagree",
      }),
    ).toBe(
      "/dashboard/develop/DS/compare/matrix?runs=A%2CB&baseline=A&group=category&show=disagree",
    );
    expect(
      matrixHref({
        mode: "experiment",
        datasetId: "DS",
        experimentId: "E9",
        row: "R1",
      }),
    ).toBe("/dashboard/develop/experiment/E9/matrix?row=R1");
  });

  it("byDatasetHref and workbenchHref", () => {
    expect(
      byDatasetHref({
        datasetId: "DS",
        runIds: ["A"],
        cand: "B",
        configKey: "k",
      }),
    ).toBe(
      "/dashboard/develop/DS/compare/summary?runs=A&view=workbench&across=datasets&cand=B&config=k",
    );
    expect(
      workbenchHref({
        datasetId: "DS",
        runIds: ["A", "B"],
        measure: "pass_at_1",
        cols: "harness",
        chart: "bars",
      }),
    ).toBe(
      "/dashboard/develop/DS/compare/summary?runs=A%2CB&view=workbench&measure=pass_at_1&cols=harness&chart=bars",
    );
  });

  it("compareTracesHref keeps the other params", () => {
    expect(
      compareTracesHref("/dashboard/observe/P/llm-tracing?tab=traces", [
        "t1",
        "t2",
      ]),
    ).toBe("/dashboard/observe/P/llm-tracing?tab=traces&compareTraces=t1%2Ct2");
  });

  // The ten Iterate entry points build the same one-shot params.
  const ENTRIES = [
    ["Iterate button", "?runs=A,B", { runId: "B" }, "iterate=B&scope=all"],
    [
      "Matrix column menu",
      "?runs=A,B&group=category",
      { runId: "B", scope: "failed" },
      "iterate=B&scope=failed",
    ],
    [
      "Trajectory footer",
      "?row=R7&dtab=trajectory",
      { runId: "B", scope: "row", rows: ["R7"] },
      "iterate=B&scope=row&rows=R7",
    ],
    [
      "PatternCallout",
      "",
      { runId: "B", scope: "rows", rows: ["R1", "R2"], cause: "pat1" },
      "iterate=B&scope=rows&rows=R1%2CR2&cause=pat1",
    ],
    [
      "RootCauseCard",
      "?view=overview",
      {
        runId: "B",
        scope: "rows",
        rows: ["R3"],
        change: "tighten_prompt",
        cause: "rc1",
      },
      "iterate=B&scope=rows&rows=R3&change=tighten_prompt&cause=rc1",
    ],
    [
      "gap slice",
      "?view=workbench",
      { runId: "B", scope: "filter" },
      "iterate=B&scope=filter",
    ],
    [
      "results SelectionBar",
      "?filters=x",
      { runId: "B", scope: "rows", rows: ["R1"] },
      "iterate=B&scope=rows&rows=R1",
    ],
    [
      "leaderboard SelectionBar",
      "?tab=experiments",
      { runId: "B" },
      "iterate=B&scope=all",
    ],
    [
      "RunRowMenu",
      "?tab=experiments",
      { runId: "B", scope: "failed" },
      "iterate=B&scope=failed",
    ],
    [
      "Isolate",
      "?runs=A,B",
      { runId: "B", change: "revert_model" },
      "iterate=B&scope=all&change=revert_model",
    ],
  ];
  it.each(ENTRIES)("%s builds the iterate shape", (_, search, args, tail) => {
    const href = iterateHref(search, args);
    expect(href.startsWith("?")).toBe(true);
    expect(href.endsWith(tail)).toBe(true);
    const r2 = readR2Params(href);
    expect(r2.iterate).toBe("B");
    expect(r2.scope).toBe(args.scope ?? "all");
  });

  it("iterateHref replaces stale one-shot params and keeps a path", () => {
    expect(
      iterateHref("/x/compare/data?runs=A&iterate=OLD&scope=row&rows=R1", {
        runId: "B",
      }),
    ).toBe("/x/compare/data?runs=A&iterate=B&scope=all");
    expect(iterateHref("?a=1", { runId: "B", scope: "bogus" })).toBe(
      "?a=1&iterate=B&scope=all",
    );
  });
});

describe("readR2Params", () => {
  it("defaults", () => {
    expect(readR2Params("")).toEqual({
      iterate: null,
      scope: null,
      rows: [],
      change: null,
      cause: null,
      across: "runs",
      vs: null,
      view: "overview",
      rowsAxis: null,
      cols: "run",
      measure: null,
      chart: "distribution",
      cand: null,
      compareTraces: null,
      config: null,
      group: "none",
      sort: null,
      show: "all",
    });
  });
  it("validates enums and groups", () => {
    const r = readR2Params(
      "view=workbench&rows=category&cols=harness&chart=heatmap&show=flaky&group=meta:tier&across=datasets",
    );
    expect(r).toMatchObject({
      view: "workbench",
      rowsAxis: "category",
      rows: [],
      cols: "harness",
      chart: "heatmap",
      show: "flaky",
      group: "meta:tier",
      across: "datasets",
    });
    expect(
      readR2Params("view=nope&cols=nope&chart=pie&show=x&group=bad").view,
    ).toBe("overview");
    expect(readR2Params("cols=nope").cols).toBe("run");
    expect(readR2Params("group=path:metadata:customer.tier").group).toBe(
      "path:metadata:customer.tier",
    );
    expect(readR2Params("group=bogus").group).toBe("none");
    expect(readR2Params("compareTraces=a").compareTraces).toBeNull();
    expect(readR2Params("compareTraces=a,b").compareTraces).toEqual(["a", "b"]);
    expect(
      readR2Params({ iterate: "B", scope: "rows", rows: "R1,R2" }),
    ).toMatchObject({
      iterate: "B",
      scope: "rows",
      rows: ["R1", "R2"],
    });
  });
});
