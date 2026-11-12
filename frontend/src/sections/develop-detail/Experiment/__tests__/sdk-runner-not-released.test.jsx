// The SDK runner (`futureagi run <file>:<attr> --run <uuid>`) is in no
// released package (Python SDK 0.7.0, CLI v0.1.0), so a copied line gives
// "command not found". While SDK_RUNNER_RELEASED is false (the product
// default, not mocked here) no screen prints it: a created code run waiting
// on the user's terminal says "Not available in this release" where its
// command was, and everything else on the panel stays.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import axios from "src/utils/axios";
import WaitingForResultsPanel from "../WaitingForResultsPanel";
import {
  launchedHowLine,
  terminalCommandOf,
  terminalRunOf,
  waitingOf,
} from "../waitingModel";

const { store, navigate } = vi.hoisted(() => ({ store: {}, navigate: vi.fn() }));
vi.mock("react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ dataset: "dataset-1" }),
}));
vi.mock("../../states", () => ({
  useRunExperimentStore: { getState: () => store },
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  endpoints: { develop: {
    r4: { runActivity: (id) => `/model-hub/r4/runs/${id}/activity/` },
    experiment: { getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/` },
  } },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSources/SourceLogo", () => ({ default: () => null }));
vi.mock("src/sections/common/RunSources/PathTabs", () => ({ default: () => null }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "text" }),
}));
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({ evalCount: 0 }),
}));
/* eslint-disable react/prop-types */
vi.mock("src/components/tooltip", () => ({ default: ({ children }) => <>{children}</> }));
vi.mock("src/sections/common/ux/CopyButton", () => ({
  default: ({ value }) => <button type="button" data-testid="copy-command" data-value={value}>Copy command</button>,
}));
vi.mock("src/sections/common/RunSources/ScriptBlock", () => ({
  default: ({ text }) => <pre>{text}</pre>,
}));
/* eslint-enable react/prop-types */

const RUN = "94ef9494-49d7-4f1f-91a6-524e25442d66";
const RUN_URL = `/model-hub/experiment-runs/${RUN}/`;
const LINE = "Not available in this release";
const CODE_LOCAL = {
  kind: "code",
  framework: { id: "langchain" },
  execution: { mode: "local" },
  limits: { tries: 3, concurrency: 2, timeout_s: 90 },
  row_labels: ["T2", "T5"],
};
const envelope = (result) => ({ status: 200, data: { status: true, result } });
let client;

const tick = async (ms = 10) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  focusManager.setFocused(true);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  store.launched = {
    experimentId: "experiment-1", datasetName: "Questions", examples: 1, trials: 1,
    runs: [{ run_id: RUN, label: "#1", name: "LangChain", target_type: "external" }],
    r4: [{ entryId: "langchain", kind: "code", mode: "local", arrival: null, config: CODE_LOCAL }],
  };
  store.setLaunched = vi.fn((value) => { store.launched = value; });
  const run = {
    id: RUN, status: "draft", plan_size: 1, completed_at: null,
    item_counts: { not_started: 1, pending: 0, running: 0, completed: 0, failed: 0, skipped: 0 },
  };
  axios.get.mockImplementation(async (url) => {
    if (url === RUN_URL) return envelope(run);
    if (url.startsWith(`${RUN_URL}items/?`))
      return envelope({ run_id: RUN, run_status: run.status, items: [], total: 0,
        limit: 20, offset: 0, has_more: false });
    throw new Error(`Unexpected read: ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("SDK runner not released: no `futureagi run` line", () => {
  const created = { run_id: RUN, label: "#1", trials: 3, config: CODE_LOCAL };

  it("a created local code run has no command and says Not available in this release", () => {
    const wait = waitingOf(created, { entryId: "langchain", mode: "local", config: CODE_LOCAL });
    expect(wait.mode).toBe("local");
    expect(wait.title).toBe("#1 waits for your terminal");
    expect(wait.simulate?.label).toBe("Simulate your terminal");
    expect(wait.command).toBeNull();
    expect(wait.unavailable).toBe(LINE);
    expect(waitingOf(created).command).toBeNull();
    expect(waitingOf(created).unavailable).toBe(LINE);
    expect(terminalRunOf(created)).toBeNull();
    expect(terminalCommandOf(created)).toBeNull();
  });

  it("runs that never printed a terminal command gain no line", () => {
    expect(waitingOf({ ...created, run_id: null }).unavailable).toBeFalsy();
    for (const mode of ["ci", "sandbox", "upload"]) {
      const wait = waitingOf({ ...created, config: { ...CODE_LOCAL, execution: { mode } } });
      expect(wait.unavailable).toBeFalsy();
    }
  });

  it("the drawer's line under its title promises no command below", () => {
    const line = launchedHowLine([{ mode: "local" }]);
    expect(line).not.toMatch(/command below/);
    expect(line).toContain(LINE);
    expect(launchedHowLine([{ mode: "ci" }])).toMatch(/^Commit the workflow/);
  });

  it("the waiting panel prints no futureagi run line and shows the line in its place", async () => {
    render(
      <QueryClientProvider client={client}>
        <WaitingForResultsPanel launched={store.launched} onDone={vi.fn()} />
      </QueryClientProvider>,
    );
    await tick();
    expect(document.body.textContent).not.toMatch(/futureagi run/);
    expect(screen.queryByTestId("run-command-line")).toBeNull();
    const copied = screen
      .queryAllByTestId("copy-command")
      .map((b) => b.getAttribute("data-value") ?? "");
    expect(copied.filter((v) => /futureagi/.test(v))).toEqual([]);
    expect(screen.getByTestId("run-command-unavailable").textContent).toContain("Run command");
    expect(screen.getByText(LINE)).toBeTruthy();
    // Nothing else on the panel goes: the run, its progress and Done stay.
    expect(screen.getByTestId("local-run-waiting").textContent).toContain("#1 · LangChain");
    expect(screen.getByTestId("persisted-run-progress").textContent).toContain("0 of 1");
    expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
  });
});
