import { describe, expect, it } from "vitest";
import {
  matchLineText,
  matchRequest,
  tracesIssues,
} from "../linkedTracesModel";

const LP = { id: "8e-11", type: "experiment" };
const OBS = { id: "8e-01", type: "observe" };

describe("matchRequest", () => {
  it("experiment project: version + metadata key", () => {
    expect(
      matchRequest({
        datasetId: "ds",
        project: LP,
        traces: {
          project_id: "8e-11",
          project_version_id: "nightly-2026-10-14",
          match: { by: "metadata", key: "row_id" },
          max_trials: 3,
        },
      }),
    ).toEqual({
      source: {
        kind: "traces",
        project_id: "8e-11",
        project_version: "nightly-2026-10-14",
      },
      shape: "match_rows",
      target: { dataset_id: "ds" },
      match: { by: "metadata", key: "row_id" },
      max_trials: 3,
    });
  });
  it("waits for a project / version / column", () => {
    expect(matchRequest({ datasetId: "ds", traces: {}, project: null })).toBe(
      null,
    );
    expect(
      matchRequest({
        datasetId: "ds",
        project: LP,
        traces: { project_id: "8e-11" },
      }),
    ).toBe(null);
    expect(
      matchRequest({
        datasetId: "ds",
        project: OBS,
        traces: { project_id: "8e-01", match: { by: "input" } },
      }),
    ).toBe(null);
  });
  it("observe project: last 7 days", () => {
    const r = matchRequest({
      datasetId: "ds",
      project: OBS,
      traces: {
        project_id: "8e-01",
        match: { by: "input", column: "instruction" },
      },
    });
    expect(r.source.filters).toEqual({ window: "7d" });
    expect(r.match).toEqual({ by: "input", column: "instruction" });
  });
});

it("matchLineText: the pinned line", () => {
  expect(
    matchLineText({
      matched: 28,
      unmatched_row_ids: ["r13", "r19"],
      unmatched_row_labels: ["T13", "T19"],
      rows_with_more_than_trials: 0,
    }),
  ).toBe(
    "28 of 30 rows matched · 2 not matched (T13, T19) · 0 rows with more traces than trials",
  );
  expect(matchLineText(null)).toBe("");
});

it("tracesIssues", () => {
  expect(tracesIssues({ traces: {} })).toEqual([
    "Pick the project your script traced to",
  ]);
  expect(
    tracesIssues({ traces: { project_id: "x" }, _matchedRows: 0 }),
  ).toEqual(["No rows matched yet"]);
  expect(
    tracesIssues({ traces: { project_id: "x" }, _matchedRows: 28 }),
  ).toEqual([]);
});
