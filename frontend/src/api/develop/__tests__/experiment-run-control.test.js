// F06 run control: the panel's read goes through the generated contract.
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
vi.mock("src/utils/axios", () => ({ default: { get } }));
vi.mock("../experiment-run-actions", () => ({
  resumeRuns: vi.fn(),
  stopRuns: vi.fn(),
}));

import { readRunControl } from "../experiment-run-control";

const EXP = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a";

beforeEach(() => get.mockReset());

describe("F06 run-control read", () => {
  it("reads the contracted run-control path of one experiment", async () => {
    const { signal } = new AbortController();
    get.mockResolvedValue({ data: { result: { runs: [] } } });
    await readRunControl(EXP, signal);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith(
      `/model-hub/experiments/v2/${EXP}/run-control/`,
      { signal },
    );
  });

  it("sends no request without an experiment id", () => {
    expect(() => readRunControl(undefined)).toThrow(
      'Missing API path param "experiment_id"',
    );
    expect(get).not.toHaveBeenCalled();
  });
});
