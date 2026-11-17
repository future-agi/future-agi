import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { useDatasetRuns } from "src/api/develop/runs";
import { useRunCommands } from "../providers";

vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetRuns: vi.fn(),
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));

const datasetId = "d5000000-0000-4000-8000-000000000001";
const otherDatasetId = "d5000000-0000-4000-8000-000000000002";
const sdkRunId = "a5000000-0000-4000-8000-000000000001";
const nativeRunId = "a5000000-0000-4000-8000-000000000002";
const experimentId = "e5000000-0000-4000-8000-000000000001";
const data = {
  results: [
    { run_id: sdkRunId, experiment_id: null, name: "SDK sample" },
    {
      run_id: nativeRunId,
      experiment_id: experimentId,
      name: "Native sample",
    },
  ],
};

beforeEach(() => {
  useDatasetRuns.mockReturnValue({ data });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("palette run destinations", () => {
  it("links SDK runs to their panel and native runs to experiment Data", () => {
    const { result } = renderHook(() => useRunCommands(datasetId));

    expect(result.current.map(({ id, href }) => ({ id, href }))).toEqual([
      {
        id: `run.${sdkRunId}`,
        href: `/dashboard/develop/${datasetId}?tab=experiments&run=${sdkRunId}`,
      },
      {
        id: `run.${nativeRunId}`,
        href: `/dashboard/develop/experiment/${experimentId}/data`,
      },
    ]);
  });

  it("updates the SDK destination when the dataset changes", () => {
    const { result, rerender } = renderHook((id) => useRunCommands(id), {
      initialProps: datasetId,
    });
    expect(result.current[0].href).toBe(
      `/dashboard/develop/${datasetId}?tab=experiments&run=${sdkRunId}`,
    );

    rerender(otherDatasetId);
    expect(result.current[0].href).toBe(
      `/dashboard/develop/${otherDatasetId}?tab=experiments&run=${sdkRunId}`,
    );
  });
});
