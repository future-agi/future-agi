import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
const state = vi.hoisted(() => ({
  post: vi.fn().mockResolvedValue({ data: { result: {} } }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: state.post },
  endpoints: { develop: { r4: { simulateUpload: () => "/simulate" } } },
}));
vi.mock("src/utils/prototype", async (original) => ({
  ...(await original()),
  isPrototypeMode: () => true,
}));
vi.mock("src/api/develop/r4", () => ({
  useRunActivity: () => ({ data: null }),
}));
vi.mock("src/sections/develop-detail/Experiment/ExperimentFormContext", () => ({
  useExperimentForm: () => ({ evalCount: 0 }),
}));
vi.mock("../pickerUi", () => ({ useWizardLayer: () => {} }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
import TodayStatus from "../TodayStatus";
afterEach(() => {
  cleanup();
  state.post.mockClear();
});
it("cannot simulate an external result from its button, footer ref or automatic start", () => {
  const simulateRef = { current: null };
  render(
    <TodayStatus
      runId="run-a"
      rowCount={3}
      autoStart
      simulateRef={simulateRef}
    />,
  );
  expect(screen.queryByTestId("simulate-upload")).not.toBeInTheDocument();
  expect(simulateRef.current).toBeNull();
  expect(state.post).not.toHaveBeenCalled();
});
