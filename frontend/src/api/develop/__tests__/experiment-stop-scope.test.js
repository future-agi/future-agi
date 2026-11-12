import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  prototype: false,
  stopDefinition: "ModelHubEmptyRequest",
  fields: { ModelHubEmptyRequest: [] },
  post: vi.fn(),
  bodyDefinitionOf: vi.fn(),
}));

vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => state.prototype,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: state.post },
  endpoints: {
    develop: {
      experiment: {
        stop: (id) => `/model-hub/experiments/v2/${id}/stop/`,
      },
    },
  },
}));
vi.mock("src/api/contracts/strictBody", () => ({
  bodyDefinitionOf: state.bodyDefinitionOf,
  unknownFields: (body, definition) =>
    Object.keys(body).filter(
      (field) => !(state.fields[definition] ?? []).includes(field),
    ),
}));

import { perRunStopSupported, stopRuns } from "../experiment-run-actions";

const experiment = "a960d98a-1f39-4409-ae70-476b3f8eae37";
const selectedRun = "744c3504-03f7-40df-b187-6e786a622e60";

beforeEach(() => {
  state.prototype = false;
  state.stopDefinition = "ModelHubEmptyRequest";
  state.fields = { ModelHubEmptyRequest: [] };
  state.post.mockReset();
  state.post.mockResolvedValue({ data: { result: {} } });
  state.bodyDefinitionOf.mockReset();
  state.bodyDefinitionOf.mockImplementation(() => state.stopDefinition);
});

describe("experiment stop request scope", () => {
  it("never widens an unsupported selected-run stop to the whole experiment", async () => {
    await expect(stopRuns(experiment, [selectedRun])).rejects.toThrow(
      /stopping individual runs is not available/i,
    );
    expect(state.post).not.toHaveBeenCalled();
  });

  it("retains the explicit whole-experiment operation when run IDs are omitted", async () => {
    await stopRuns(experiment);
    expect(state.post).toHaveBeenCalledExactlyOnceWith(
      `/model-hub/experiments/v2/${experiment}/stop/`,
      {},
    );
  });

  it.each([
    { label: "empty selection", selection: [] },
    { label: "null selection", selection: null },
    { label: "non-array selection", selection: "run" },
    { label: "undefined ID", selection: [undefined] },
    { label: "empty ID", selection: [""] },
    { label: "blank ID", selection: ["   "] },
    { label: "sparse selection", selection: Array(1) },
  ])("refuses $label instead of broadening it", async ({ selection }) => {
    state.prototype = true;
    await expect(stopRuns(experiment, selection)).rejects.toThrow(
      /select at least one run/i,
    );
    expect(state.post).not.toHaveBeenCalled();
  });

  it("reads selected-run support from this endpoint's request definition", async () => {
    state.stopDefinition = "ExperimentStopRequest";
    state.fields.ExperimentStopRequest = ["run_ids"];
    expect(perRunStopSupported()).toBe(true);
    expect(state.bodyDefinitionOf).toHaveBeenCalledWith(
      "post",
      "/model-hub/experiments/v2/{experiment_id}/stop/",
    );
    await stopRuns(experiment, [selectedRun]);
    expect(state.post).toHaveBeenCalledExactlyOnceWith(
      `/model-hub/experiments/v2/${experiment}/stop/`,
      { run_ids: [selectedRun] },
    );
  });

  it("does not infer support from an unrelated empty-body definition", () => {
    state.stopDefinition = "OtherStopRequest";
    state.fields.ModelHubEmptyRequest = ["run_ids"];
    state.fields.OtherStopRequest = [];
    expect(perRunStopSupported()).toBe(false);
  });

  it("fails closed when this endpoint has no request definition", () => {
    state.stopDefinition = null;
    expect(perRunStopSupported()).toBe(false);
  });

  it("keeps selected-run requests intact in prototype mode", async () => {
    state.prototype = true;
    await stopRuns(experiment, [selectedRun]);
    expect(state.post).toHaveBeenCalledExactlyOnceWith(
      `/model-hub/experiments/v2/${experiment}/stop/`,
      { run_ids: [selectedRun] },
    );
  });
});
