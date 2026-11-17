import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, screen } from "src/utils/test-utils";
import RerunWithChangesHost from "../RerunWithChangesHost";

const state = vi.hoisted(() => ({ prototype: false, request: null }));
const datasetId = "00000000-0000-4000-8000-000000000001";
const experimentId = "00000000-0000-4000-8000-000000000002";
const run = { run_id: "run-a", kind: "prompt", experiment_id: experimentId };
vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => state.prototype,
}));
vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => true }));
vi.mock("../rerunBus", () => ({
  useRerunRequest: () => ({ request: state.request, close: vi.fn() }),
  openRerunWithChanges: vi.fn(),
}));
vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: (id) => ({
    data: id ? { dataset_id: datasetId } : null,
  }),
}));
vi.mock("src/api/develop/runs", () => ({
  useDatasetRuns: (_, options) => ({
    data: options.enabled ? { results: [run] } : null,
  }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: vi.fn(),
}));
vi.mock("../RerunWithChangesOpen", () => ({
  default: ({ run: selected }) => (
    <div data-testid="opened-rerun">{selected.run_id}</div>
  ),
}));
beforeEach(() => {
  state.request = { runId: "run-a" };
});
afterEach(cleanup);

it.each([false, true])(
  "opens a registered rerun request with prototype=%s",
  async (prototype) => {
    state.prototype = prototype;
    renderWithRouter(<RerunWithChangesHost />, {
      route: `/dashboard/develop/experiment/${experimentId}/data`,
    });
    expect(await screen.findByTestId("opened-rerun")).toHaveTextContent(
      "run-a",
    );
  },
);
