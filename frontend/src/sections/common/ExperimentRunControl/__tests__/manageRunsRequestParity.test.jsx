// Manage runs where run control answers: it makes exactly the requests it
// made before it learned to handle a server without run control. Opened, it
// reads the runs and reads again every 1.5 s; a run's Stop names that run;
// its Resume names that run; it never sends the empty-body stop. The
// component is mounted only by the original results view's bar
// (experiment-detail-classic/ExperimentBar.jsx), which is what the experiment
// page shows with the launch switch off and what it shows with the switch on
// for an experiment the new page cannot read; both are pinned here through
// that bar. Requests are recorded at the API client, through the real
// request functions. The file imports nothing new, so it runs unchanged on
// the build before that handling to compare.
/* eslint-disable react/prop-types */
import React from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import ExperimentBar from "src/sections/experiment-detail-classic/ExperimentBar";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const SEL = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const SIB = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";

const api = vi.hoisted(() => ({
  sent: [],
  status: {},
  runs() {
    return [
      { run_id: SEL, name: "First variant", status: this.status[SEL] },
      { run_id: SIB, name: "Second variant", status: this.status[SIB] },
    ];
  },
}));
const unreleased = vi.hoisted(() => ({ shown: true }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) => {
      api.sent.push({ method: "get", url });
      return {
        status: 200,
        data: { status: true, result: { runs: api.runs() } },
      };
    },
    post: async (url, body) => {
      api.sent.push({ method: "post", url, body });
      if (url.endsWith("/stop/") && body?.run_ids)
        body.run_ids.forEach((id) => (api.status[id] = "STOPPED"));
      if (url.endsWith("/resume/"))
        body.run_ids.forEach((id) => (api.status[id] = "RUNNING"));
      return {
        status: 202,
        data: {
          status: true,
          result: {
            experiment_id: EXP,
            runs: api.runs().filter((r) => body?.run_ids?.includes(r.run_id)),
          },
        },
      };
    },
  },
  endpoints: {
    develop: {
      experiment: {
        stop: (id) => `/model-hub/experiments/v2/${id}/stop/`,
      },
      datasetRuns: {
        resume: (id) => `/model-hub/experiments/v2/${id}/resume/`,
      },
    },
  },
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

const runControlUrl = `/model-hub/experiments/v2/${EXP}/run-control/`;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function openBar() {
  return render(
    <MemoryRouter
      initialEntries={[`/dashboard/develop/experiment/${EXP}/data`]}
    >
      <Routes>
        <Route
          path="/dashboard/develop/experiment/:experimentId/data"
          element={<ExperimentBar />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  api.sent.length = 0;
  api.status = { [SEL]: "RUNNING", [SIB]: "RUNNING" };
});
afterEach(() => {
  setLaunchOverrideForTest(null);
});

describe.each([
  { state: "switch on (shipped build)", shown: true },
  { state: "switch off", shown: false },
])("Manage runs where run control answers, $state", ({ shown }) => {
  it("reads, re-reads, stops and resumes only the chosen run", async () => {
    unreleased.shown = shown;
    setLaunchOverrideForTest(
      shown
        ? { launch: true, experimentsBasic: true }
        : { launch: true, showUnreleased: false, experimentsBasic: true },
    );
    openBar();
    fireEvent.click(screen.getByRole("button", { name: "Manage runs" }));
    const selected = await screen.findByTestId(`run-control-${SEL}`);
    expect(api.sent[0]).toEqual({ method: "get", url: runControlUrl });
    // It keeps reading while open.
    await pause(1700);
    expect(api.sent.filter((r) => r.method === "get").length).toBeGreaterThan(
      1,
    );

    fireEvent.click(within(selected).getByRole("button", { name: "Stop" }));
    fireEvent.click(
      await within(selected).findByRole("button", { name: "Resume" }),
    );
    await waitFor(() =>
      expect(api.sent.filter((r) => r.method === "post")).toHaveLength(2),
    );

    expect(api.sent.filter((r) => r.method === "post")).toEqual([
      {
        method: "post",
        url: `/model-hub/experiments/v2/${EXP}/stop/`,
        body: { run_ids: [SEL] },
      },
      {
        method: "post",
        url: `/model-hub/experiments/v2/${EXP}/resume/`,
        body: { run_ids: [SEL] },
      },
    ]);
    // Every read is the run-control read; nothing else is sent.
    api.sent
      .filter((r) => r.method === "get")
      .forEach((r) => expect(r.url).toBe(runControlUrl));
    expect(
      within(screen.getByTestId(`run-control-${SIB}`)).getByText("Running"),
    ).toBeInTheDocument();
  });
});
