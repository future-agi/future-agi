import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const state = vi.hoisted(() => ({ prototype: false }));
const actions = vi.hoisted(() => ({
  pending: false,
  simulateUpload: vi.fn(),
}));
const useRunActions = vi.hoisted(() => vi.fn());

vi.mock("src/utils/prototype", async (orig) => ({
  ...(await orig()),
  isPrototypeMode: () => state.prototype,
}));
vi.mock("src/sections/common/Runs/useRunActions", () => ({
  default: useRunActions,
}));

// The retired slot accepts and ignores these legacy props; keep testing them.
/** @type {{ default: React.ComponentType<{ experimentId: string, runId: string, runLabel: string, exampleIds?: string[], onSimulateUpload?: () => void }> }} */
const { default: ExternalRunSnippet } = await import("../ExternalRunSnippet");
const { default: WaitingForResultsPanel } = await import(
  "../WaitingForResultsPanel"
);

beforeEach(() => {
  vi.clearAllMocks();
  useRunActions.mockReturnValue(actions);
});
afterEach(cleanup);

const Location = () => (
  <output data-testid="location">{useLocation().pathname}</output>
);
const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        {ui}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const expectNoSimulation = () => {
  expect(screen.queryByRole("button", { name: /simulate/i })).toBeNull();
  expect(screen.queryByTestId("simulate-the-upload")).toBeNull();
  expect(screen.queryByTestId("external-run-simulate")).toBeNull();
  expect(screen.queryByTestId("simulated-tag")).toBeNull();
};

const launched = {
  experimentId: "e1",
  ordinal: 6,
  examples: 2,
  trials: 1,
  runs: [
    {
      run_id: "r1",
      label: "#6A",
      name: "External run",
      target_type: "external",
      status: "WaitingForResults",
    },
  ],
};

describe.each([false, true])("upload controls (prototype=%s)", (prototype) => {
  beforeEach(() => {
    state.prototype = prototype;
  });

  it("renders no upload command, simulation control or simulated tag", () => {
    const onSimulateUpload = vi.fn();
    wrap(
      <ExternalRunSnippet
        experimentId="e1"
        runId="r1"
        runLabel="#6A"
        exampleIds={["row-1", "row-2"]}
        onSimulateUpload={onSimulateUpload}
      />,
    );
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.queryByTestId("upload-snippet")).toBeNull();
    expect(screen.queryByTestId("download-sample-results")).toBeNull();
    expect(document.body.textContent).not.toMatch(
      /curl|pip install|python|npx|futureagi|FI_API_KEY/i,
    );
    expectNoSimulation();
    expect(onSimulateUpload).not.toHaveBeenCalled();
  });

  it("renders no control without an upload callback", () => {
    wrap(<ExternalRunSnippet experimentId="e1" runId="r1" runLabel="#6A" />);
    expectNoSimulation();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps the waiting state and navigation without creating upload actions", () => {
    const onDone = vi.fn();
    wrap(<WaitingForResultsPanel launched={launched} onDone={onDone} />);
    expect(screen.getByText("#6A · External run")).toBeTruthy();
    expect(screen.getByText(/#6A waits for/)).toBeTruthy();
    expect(screen.queryByText(/results received · scoring/)).toBeNull();
    expectNoSimulation();
    expect(useRunActions).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Done",
          exact: true,
        }),
      ),
    );
    expect(onDone).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole(
        "button",
        /** @type {import("@testing-library/react").ByRoleOptions} */ ({
          name: "Open experiment",
          exact: true,
        }),
      ),
    );
    expect(onDone).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("location").textContent).toBe(
      "/dashboard/develop/experiment/e1/data",
    );
    expect(actions.simulateUpload).not.toHaveBeenCalled();
  });
});
