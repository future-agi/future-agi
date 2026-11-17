// the capability contract, v2.1 round 1 (independent check FAIL): every status a user
// can see or export comes from the run's REAL outcome through the one
// derivation (getRunStatus / runOutcomeKey). A run stamped Completed whose
// rows all errored exports and reads Failed; a partial one reads Finished
// with errors (PartialCompleted). Never Completed.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { analysisCsv } from "src/sections/common/Analysis/analysisCsv";
import { resultsMeta } from "src/sections/common/export/resultsExport";
import { UpdatedCell } from "src/sections/develop-detail/ExperimentTab/R4Cells";
import SummaryVerdicts from "src/sections/experiment-detail/ExperimentSummary/Analysis/SummaryVerdicts";
import {
  summaryVerdicts,
  verdictMarkdown,
} from "src/sections/experiment-detail/ExperimentSummary/Analysis/verdictItems";
import { headerShipTone } from "src/sections/experiment-detail/matrix/headerVerdict";

vi.mock("src/sections/develop-detail/ExperimentTab/RunStatusCell", () => ({
  default: ({ data }) => (
    <span data-testid="lb-status-chip">{`chip:${data?.label}`}</span>
  ),
}));

// The checker's walk: #9A, an HTTP endpoint run, 20 of 20 rows errored,
// yet stamped Completed.
const BASE = {
  run_id: "A1",
  label: "#1A",
  name: "Support Reply V3 × gpt-4o-mini",
  status: "Completed",
  progress: { done: 20, total: 20, errors: 0 },
  in_view: 20,
  errored_rows: 0,
  success_rate: 0.8,
  success_pass: 16,
  success_n: 20,
  success_tasks: 20,
  updated_at: "2026-10-20T00:00:00.000Z",
};
const FAILED = {
  run_id: "A9",
  label: "#9A",
  name: "HTTP endpoint · POST 10.20.30.40/v1/reply",
  status: "Completed",
  progress: { done: 20, total: 20, errors: 20 },
  in_view: 20,
  errored_rows: 20,
  success_rate: 0,
  success_pass: 0,
  success_n: 20,
  success_tasks: 20,
  updated_at: "2026-10-25T00:00:00.000Z",
};
const PARTIAL = {
  ...FAILED,
  run_id: "A2",
  label: "#2A",
  name: "Support reply v4",
  progress: { done: 20, total: 20, errors: 3 },
  errored_rows: 3,
  success_rate: 0.65,
  success_pass: 13,
};

const analysis = {
  baseline_run_id: "A1",
  metrics: [],
  runs: [BASE, FAILED, PARTIAL],
};

const csvStatus = (text, label) => {
  const [head, ...lines] = text.split("\n");
  const col = head.split(",").indexOf("status");
  const line = lines.find((l) => l.startsWith(`${label},`));
  return line.split(",")[col];
};

describe("exports read the run's real outcome", () => {
  it("Summary / compare CSV: failed exports Failed, partial PartialCompleted", () => {
    const text = analysisCsv(analysis);
    expect(csvStatus(text, "#9A")).toBe("Failed");
    expect(csvStatus(text, "#2A")).toBe("PartialCompleted");
    expect(csvStatus(text, "#1A")).toBe("Completed");
    expect(text.split("\n").filter((l) => /,Completed,/.test(l))).toHaveLength(
      1,
    );
  });

  it("Results Download header (CSV/JSON/JSONL): runs[].status is the outcome", () => {
    const meta = resultsMeta({
      dataset: "GS",
      view: {},
      summary: { runs: [BASE, FAILED, PARTIAL], metrics: [], baseline: "A1" },
      exportedAt: "x",
      scope: "all",
    });
    const byRun = Object.fromEntries(meta.runs.map((r) => [r.run, r.status]));
    expect(byRun).toEqual({
      "#1A": "Completed",
      "#9A": "Failed",
      "#2A": "PartialCompleted",
    });
  });
});

describe("the leaderboard Updated cell names the outcome", () => {
  const cell = (data) => render(<UpdatedCell node={{ data }} data={data} />);

  it("a failed run shows its status chip, not a time", () => {
    cell(FAILED);
    expect(screen.getByTestId("lb-status-chip").textContent).toBe("chip:#9A");
  });

  it("a run that finished with errors shows its status chip too", () => {
    cell(PARTIAL);
    expect(screen.getByTestId("lb-status-chip").textContent).toBe("chip:#2A");
  });

  it("a clean Completed run keeps its Updated time", () => {
    cell(BASE);
    expect(screen.queryByTestId("lb-status-chip")).toBeNull();
  });
});

describe("compare names a failed candidate Failed", () => {
  const cmp = { baseline_run_id: "A1", metrics: [], runs: [BASE, FAILED] };

  it("the verdict card reads Failed, not Worse or a regression", () => {
    render(<SummaryVerdicts analysis={cmp} candidateId="A9" />);
    const row = screen.getByTestId("summary-failed-run");
    expect(row.textContent).toContain("Failed");
    expect(row.textContent).toContain(
      "#9A HTTP endpoint failed: all 20 rows errored",
    );
    const card = screen.getByTestId("summary-verdicts").textContent;
    expect(card).not.toMatch(/\bWorse\b|trails|Don't ship|Completed/);
  });

  it("Copy summary names it Failed", () => {
    const md = verdictMarkdown(cmp, summaryVerdicts(cmp));
    expect(md).toContain("- #9A HTTP endpoint failed: all 20 rows errored");
    expect(md).not.toMatch(/trails/);
  });

  it("the header does not lead with Test a fix for a failed candidate", () => {
    expect(
      headerShipTone({
        matrix: {},
        runIds: ["A1", "A9"],
        baselineId: "A1",
        cand: "A9",
        runs: [BASE, FAILED],
      }),
    ).toBeNull();
  });
});

describe("outcomes in filters, grid values, and upload status", () => {
  it("the runs filter `status = Completed` never matches a failed or partial run", async () => {
    const { filterRuns } = await import(
      "src/sections/common/filters/runsScope"
    );
    const ids = (expr) => {
      const res = filterRuns(expr, [BASE, FAILED, PARTIAL]);
      expect(res.error).toBeUndefined();
      return res.runs.map((r) => r.label);
    };
    expect(ids('status = "Completed"')).toEqual(["#1A"]);
    expect(ids('status = "Failed"')).toEqual(["#9A"]);
    expect(ids('status = "PartialCompleted"')).toEqual(["#2A"]);
  });

  it("an upload whose rows all errored reads Failed; partial never Completed", async () => {
    const { uploadStatus } = await import(
      "src/sections/develop-detail/Experiment/runFromCodeModel"
    );
    expect(uploadStatus(FAILED).text).toBe("Failed");
    expect(uploadStatus(PARTIAL).text).toBe(
      "Finished with errors · 20 results received",
    );
    expect(uploadStatus(BASE).text).toBe("Completed · 20 results received");
  });
});
