import { describe, expect, it } from "vitest";

import { datasetRunsKey, datasetRunsParams } from "../runs";
import { endpoints } from "src/utils/axios";

describe("dataset runs hooks", () => {
  it("maps options to R1 query keys and drops empty ones", () => {
    expect(datasetRunsParams()).toEqual({ limit: 500 });
    expect(
      datasetRunsParams({
        include: "metrics",
        baseline: "run-1",
        versionId: "all",
        search: "agent",
        status: "Running",
        sameTargetAs: "run-2",
        limit: 50,
      }),
    ).toEqual({
      include: "metrics",
      baseline: "run-1",
      dataset_version_id: "all",
      search: "agent",
      status: "Running",
      same_target_as: "run-2",
      limit: 50,
    });
  });

  it("keys runs per dataset so a pin invalidates every view of it", () => {
    expect(datasetRunsKey("ds-1")).toEqual(["develop", "dataset-runs", "ds-1"]);
  });

  it("builds the prototype endpoint paths from the registry", () => {
    expect(endpoints.develop.datasetRuns.list("ds 1")).toBe(
      "/model-hub/datasets/ds%201/runs/",
    );
    expect(endpoints.develop.datasetRuns.baseline("ds")).toBe(
      "/model-hub/datasets/ds/baseline/",
    );
    expect(endpoints.develop.datasetRuns.uploadResults("e1", "r1")).toBe(
      "/model-hub/experiments/v2/e1/runs/r1/results/",
    );
    expect(endpoints.develop.versions.changes("ds", "v6")).toBe(
      "/model-hub/datasets/ds/versions/v6/changes/",
    );
    expect(endpoints.develop.datasetRows.fromResults("ds2")).toBe(
      "/model-hub/datasets/ds2/rows/from-results/",
    );
  });
});
