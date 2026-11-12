import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { useRerunResultCells } from "./run-results";

vi.mock("@tanstack/react-query", () => ({
  useMutation: (options) => options,
  useQuery: vi.fn(),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    develop: {
      experiment: {
        reRunExperimentCell: (id) => `/experiments/${id}/rerun-cells/`,
      },
    },
  },
}));
vi.mock("src/sections/common/Runs/runStatus", () => ({ pollIntervalFor: vi.fn() }));
vi.mock("src/sections/common/Results/filterPills", () => ({ serializePills: vi.fn() }));
vi.mock("src/api/develop/runs", () => ({
  fetchReadPages: vi.fn(),
  readEnvelope: vi.fn(),
}));

describe("Results rerun identity (W10-EXPERIMENTS-DATASETS-L02)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.post.mockResolvedValue({ data: { status: true } });
  });

  it("sends canonical run/example selectors, never example IDs as snapshot row IDs", async () => {
    const mutation = useRerunResultCells("dataset");
    await mutation.mutationFn([
      {
        run: { run_id: "run-a", experiment_id: "experiment-a", output_column_id: "output-a" },
        exampleId: "example-a",
      },
      {
        run: { run_id: "run-b", experiment_id: "experiment-a", output_column_id: "output-b" },
        exampleId: "example-a",
      },
      {
        run: { run_id: "run-c", experiment_id: "experiment-b", output_column_id: "output-c" },
        exampleId: "example-b",
      },
    ]);

    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post).toHaveBeenNthCalledWith(1, "/experiments/experiment-a/rerun-cells/", {
      cells: [
        { column_id: "output-a", run_id: "run-a", example_id: "example-a" },
        { column_id: "output-b", run_id: "run-b", example_id: "example-a" },
      ],
      failed_only: true,
    });
    expect(axios.post).toHaveBeenNthCalledWith(2, "/experiments/experiment-b/rerun-cells/", {
      cells: [{ column_id: "output-c", run_id: "run-c", example_id: "example-b" }],
      failed_only: true,
    });
  });
});
