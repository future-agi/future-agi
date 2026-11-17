import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import axios from "src/utils/axios";
import WaitingForResultsPanel from "../WaitingForResultsPanel";

const { store, navigate } = vi.hoisted(() => ({ store: {}, navigate: vi.fn() }));
vi.mock("react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ dataset: "dataset-1" }),
}));
// This suite pins the released SDK runner's command; while it is unreleased
// the panel prints no command (sdk-runner-not-released.test.jsx).
vi.mock("../sdkRunner", () => ({ SDK_RUNNER_RELEASED: true }));
vi.mock("../../states", () => ({
  useRunExperimentStore: { getState: () => store },
}));
// Exercise the real query hooks, generated client and native URLs. A legacy
// activity/catalogue/ledger read throws below instead of silently succeeding.
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
const URL = `/model-hub/experiment-runs/${RUN}/`;
const ITEM = {
  id: "item-1", example_id: "example-1", trial_number: 1,
  status: "completed", output: { answer: "Stored LangChain answer" },
  output_truncated: false, error: null, error_truncated: false,
};
const envelope = (result) => ({ status: 200, data: { status: true, result } });
let client;
let run;
let items;
let total;
let failPath;

function mount() {
  return render(
    <QueryClientProvider client={client}>
      <WaitingForResultsPanel launched={store.launched} onDone={vi.fn()} />
    </QueryClientProvider>,
  );
}
const tick = async (ms = 10) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
};
const complete = (extra = {}) => {
  run = {
    ...run, status: "completed", completed_at: "2026-11-09T10:00:00Z",
    item_counts: { ...run.item_counts, completed: 1, not_started: 0 }, ...extra,
  };
  items = [ITEM];
  total = 1;
};
function expectReadOnlyNative() {
  expect(axios.get.mock.calls.length).toBeGreaterThan(0);
  for (const [url] of axios.get.mock.calls) {
    expect(url === URL || url.startsWith(`${URL}items/?`)).toBe(true);
  }
  for (const verb of ["post", "put", "patch", "delete"]) {
    expect(axios[verb]).not.toHaveBeenCalled();
  }
  expect(navigate).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  focusManager.setFocused(true);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  run = {
    id: RUN, status: "draft", plan_size: 1, completed_at: null,
    item_counts: { not_started: 1, pending: 0, running: 0, completed: 0, failed: 0, skipped: 0 },
  };
  items = [];
  total = 0;
  failPath = null;
  store.launched = {
    experimentId: "experiment-1", datasetName: "Questions", examples: 1, trials: 1,
    runs: [{ run_id: RUN, label: "#1", name: "LangChain", target_type: "external" }],
    r4: [{ entryId: "langchain", kind: "code", mode: "local", arrival: null,
      config: { kind: "code", framework: { id: "langchain" }, execution: { mode: "local" } } }],
  };
  store.setLaunched = vi.fn((value) => { store.launched = value; });
  axios.get.mockImplementation(async (url) => {
    if (failPath && url.includes(failPath)) throw new Error("Read unavailable");
    if (url === URL) return envelope(run);
    if (url.startsWith(`${URL}items/?`)) {
      // `URL` above is this run's path; parse with the global URL class.
      const q = new globalThis.URL(url, "https://example.invalid").searchParams;
      const offset = Number(q.get("offset"));
      const limit = Number(q.get("limit"));
      return envelope({ run_id: RUN, run_status: run.status,
        items: items.slice(offset, offset + limit), total, limit, offset,
        has_more: offset + limit < total });
    }
    throw new Error(`Unexpected non-native read: ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("fresh local framework run follows persisted results", () => {
  it("polls without an arrival, retains the run command and opens recorded outputs in place", async () => {
    mount();
    await tick();
    expect(axios.get).toHaveBeenCalledWith(URL, expect.anything());
    expect(screen.getByTestId("persisted-run-progress").textContent).toContain("0 of 1");
    expect(screen.getByTestId("copy-command").getAttribute("data-value")).toContain(`--run ${RUN}`);
    expect(screen.getByTestId("copy-command").getAttribute("data-value")).toMatch(/^futureagi run /);
    expect(store.launched.resultsIn).toBeUndefined();
    complete();
    await tick(2100);
    expect(screen.getByTestId("persisted-run-progress").textContent).toContain("Results in");
    expect(screen.getByTestId("persisted-run-progress").textContent).toContain("1 of 1");
    expect(store.launched.resultsIn[RUN]).toMatchObject({ done: 1, total: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Open results" }));
    expect(screen.getByTestId("persisted-run-items").textContent).toContain("Stored LangChain answer");
    expect(screen.getByTestId("persisted-run-items").textContent).not.toMatch(/passed/);
    const reads = axios.get.mock.calls.length;
    await tick(6000);
    expect(axios.get).toHaveBeenCalledTimes(reads);
    expectReadOnlyNative();
  });

  it.each([URL, "items/?"])("retries a failed %s read without starting another run", async (path) => {
    failPath = path;
    mount();
    await tick();
    expect(screen.getByRole("alert").textContent).toContain("Could not read");
    const reads = axios.get.mock.calls.length;
    await tick(6000);
    expect(axios.get).toHaveBeenCalledTimes(reads);
    expect(store.launched.resultsIn).toBeUndefined();
    complete();
    failPath = null;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await tick();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(store.launched.resultsIn[RUN].done).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Open results" }));
    expect(screen.getByTestId("persisted-run-items").textContent).toContain("Stored LangChain answer");
    expect(axios.get.mock.calls.filter(([url]) => url === URL)).toHaveLength(2);
    expectReadOnlyNative();
  });

  it("does not trust a simulated finished arrival instead of the persisted header", async () => {
    store.launched.r4[0].arrival = { finished: true, uploaded: 99 };
    mount();
    await tick();
    expect(screen.getByTestId("persisted-run-progress").textContent).toContain("0 of 1");
    expect(store.launched.resultsIn).toBeUndefined();
    complete({ status: "partial" });
    await tick(2100);
    expect(store.launched.resultsIn[RUN].done).toBe(1);
    const reads = axios.get.mock.calls.length;
    await tick(6000);
    expect(axios.get).toHaveBeenCalledTimes(reads);
    expectReadOnlyNative();
  });

  it("pages recorded items and labels truncation without converting failure into a pass", async () => {
    complete({ status: "failed", plan_size: 21,
      item_counts: { completed: 20, failed: 1, skipped: 0, not_started: 0 } });
    items = Array.from({ length: 21 }, (_, i) => ({ ...ITEM, id: `item-${i}`, example_id: `example-${i}`,
      output: i === 20 ? false : `stored-${i}` }));
    items[0] = { ...items[0], status: "failed", output: null,
      error: "recorded error prefix", error_truncated: true };
    total = items.length;
    mount();
    await tick();
    expect(screen.getByTestId("persisted-run-progress").textContent).toContain("failed");
    expect(screen.getByTestId("persisted-run-progress").textContent).not.toContain("passed");
    fireEvent.click(screen.getByRole("button", { name: "Open results" }));
    expect(screen.getByTestId("persisted-run-items").textContent).toContain("recorded error prefix");
    expect(screen.getByTestId("persisted-run-items").textContent).toContain("truncated");
    fireEvent.click(screen.getByRole("button", { name: "Next results" }));
    await tick();
    expect(screen.getByTestId("persisted-run-items").textContent).toContain("false");
    expect(screen.getByRole("button", { name: "Next results" }).disabled).toBe(true);
    expectReadOnlyNative();
  });
});
