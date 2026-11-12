// An experiment made before runs were recorded opens in the original
// results view and is listed with the original table's row actions. Their
// Stop is the production one: the empty-body stop, which names no runs and
// is not run control, so it stops the experiment on every server whatever its
// run-control setting and whichever launch-switch state the build is in.
// (When such an experiment opens in the new page instead, its Stop is pinned
// in experiment-detail/__tests__/stop-without-run-control.test.jsx.)
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const sent = vi.hoisted(() => []);
const unreleased = vi.hoisted(() => ({ shown: true }));

vi.mock("src/utils/axios", () => ({
  default: {
    post: async (url, body) => {
      sent.push({ method: "post", url, body });
      return { status: 200, data: { status: true, result: {} } };
    },
  },
  endpoints: {
    develop: {
      experiment: {
        stop: (id) => `/model-hub/experiments/v2/${id}/stop/`,
        rerun: "/model-hub/experiments/v2/re-run/",
      },
    },
  },
}));
vi.mock("src/utils/experimentRerunError", () => ({
  handleExperimentRerunError: () => undefined,
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { useStopExperiment } from "src/sections/develop-detail/Experiment/api";

const wrapper = ({ children }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

afterEach(() => {
  setLaunchOverrideForTest(null);
  sent.length = 0;
});

describe.each([
  { state: "switch on (shipped build)", shown: true },
  { state: "switch off", shown: false },
])("Stop on an experiment from before run rows, $state", ({ shown }) => {
  it("sends the empty-body stop, naming no runs", async () => {
    unreleased.shown = shown;
    setLaunchOverrideForTest(
      shown
        ? { launch: true, experimentsBasic: true }
        : { launch: true, showUnreleased: false, experimentsBasic: true },
    );
    const done = vi.fn();
    const { result } = renderHook(() => useStopExperiment(EXP, done), {
      wrapper,
    });
    act(() => result.current.stopExperiment());
    await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
    expect(sent).toEqual([
      {
        method: "post",
        url: `/model-hub/experiments/v2/${EXP}/stop/`,
        body: {},
      },
    ]);
  });
});
