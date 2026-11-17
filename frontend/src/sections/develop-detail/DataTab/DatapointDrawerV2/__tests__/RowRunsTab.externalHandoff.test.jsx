import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { resetAllStores } from "src/utils/zustand";
import RowRunsTab from "../RowRunsTab";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  uuid: vi.fn(),
  revoke: vi.fn(),
  canRun: true,
  canDownload: true,
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: h.get, post: h.post },
}));
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentDetail: () => ({ data: undefined, isFetching: false }),
  useDatasetRuns: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null, isLoading: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Collab/RunCommentsSection", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { run: h.canRun, download: h.canDownload },
  }),
}));

const DATASET = "11111111-1111-4111-8111-111111111111";
const ROW = "22222222-2222-4222-8222-222222222222";
const RUN = "44444444-4444-4444-8444-444444444444";
const VERSION = "66666666-6666-4666-8666-666666666666";
const EXAMPLE = "77777777-7777-4777-8777-777777777777";
const EXPERIMENT = "88888888-8888-4888-8888-888888888888";
const NEW_RUN = "90d5a374-6e50-4578-81fa-56cf3ca8dd01";
const ROW_URL = `/model-hub/datasets/${DATASET}/rows/${ROW}/runs/`;
const SOURCE_URL = `/model-hub/experiment-runs/${RUN}/`;
const VERSION_URL = `/model-hub/datasets/${DATASET}/versions/${VERSION}/?limit=500&offset=0`;
const rowTrials = [
  { example_id: EXAMPLE, trial_number: 2 },
  { example_id: EXAMPLE, trial_number: 7 },
];
const pinnedInput = {
  example_id: EXAMPLE,
  input: { question: "Pinned question" },
  expected: "Pinned answer",
};

// DatasetRowRunsRunSerializer's shape: target_type, not task_kind or source.
// The dataset row ID is deliberately different from this run's example ID.
const rowRun = {
  run_id: RUN,
  example_id: EXAMPLE,
  label: "#1",
  name: null,
  experiment_id: null,
  experiment_name: null,
  status: "completed",
  output: { text: "Recorded answer" },
  error: null,
  trials: 2,
  evals: {},
  latency_ms: 120,
  cost: 0.01,
  ran_at: "2026-01-02T03:04:05Z",
  dataset_version: { id: VERSION, content_hash: "" },
  is_dataset_baseline: false,
  row_changed_after: false,
  target_type: "external",
  trace_id: null,
  trials_detail: rowTrials.map(({ trial_number }) => ({
    trial_number,
    status: "completed",
    trace_id: null,
    output: { text: "Recorded answer" },
    error: null,
    scores: {},
  })),
};
const source = {
  id: RUN,
  task_kind: "external",
  dataset_version_id: VERSION,
  experiment_id: null,
  // Selecting the legacy row ID or the full run must not pass this test.
  expected_items: [
    rowTrials[0],
    { example_id: ROW, trial_number: 3 },
    rowTrials[1],
  ],
  evaluator_bindings: [{ metric_id: "metric-a", evaluator_version_id: "old" }],
  model_config: { model: "fixture" },
  metadata: { code_revision: "revision-a" },
};
const ok = (result) => ({ data: { status: true, result } });
const rowResponse = (run) => ({
  summary: {
    runs: 1,
    experiments: run.experiment_id ? 1 : 0,
    primary_metric_name: null,
    primary_scored: 0,
    primary_pass: 0,
    classic_experiments: 0,
    flags: {
      hard_case: false,
      fails_of_runs: null,
      flaky: false,
      expected_changed_after_runs: 0,
    },
  },
  metrics: [],
  runs: [run],
});

let client;
let downloads;
beforeEach(() => {
  vi.resetAllMocks();
  h.canRun = true;
  h.canDownload = true;
  h.uuid.mockReturnValue(NEW_RUN);
  vi.stubGlobal("crypto", { randomUUID: h.uuid });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  downloads = [];
  vi.stubGlobal(
    "Blob",
    class extends Blob {
      constructor(parts, options) {
        super(parts, options);
        downloads.push(JSON.parse(parts[0]));
      }
    },
  );
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL() {
        return "blob:handoff";
      }
      static revokeObjectURL = h.revoke;
    },
  );
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  client.clear();
  resetAllStores();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const mount = async (overrides = {}) => {
  const run = { ...rowRun, ...overrides };
  h.get.mockImplementation((url) => {
    if (url === ROW_URL) return Promise.resolve(ok(rowResponse(run)));
    if (url === SOURCE_URL)
      return Promise.resolve(
        ok({ ...source, experiment_id: run.experiment_id }),
      );
    if (url === VERSION_URL)
      return Promise.resolve(
        ok({
          version: { id: VERSION, dataset: DATASET, item_count: 2 },
          items: [
            pinnedInput,
            { example_id: ROW, input: "Another example", expected: null },
          ],
          offset: 0,
          limit: 500,
          has_more: false,
        }),
      );
    return Promise.reject(new Error(`Unexpected read: ${url}`));
  });
  renderWithRouter(
    <QueryClientProvider client={client}>
      <RowRunsTab datasetId={DATASET} rowId={ROW} />
    </QueryClientProvider>,
    { route: `/dashboard/develop/${DATASET}?row=${ROW}` },
  );
  fireEvent.click(await screen.findByRole("button", { name: "More for #1" }));
};

const action = () =>
  screen.getByRole("menuitem", { name: "Run again in your code…" });

describe("row Runs external execution handoff", () => {
  it.each([
    ["attached", EXPERIMENT, "Open this result in the experiment"],
    ["standalone", null, "Open this result"],
  ])(
    "opens the %s run with only this example and its original trials",
    async (_, experimentId, resultLabel) => {
      await mount({
        experiment_id: experimentId,
        experiment_name: experimentId ? "Recorded experiment" : null,
      });
      expect(
        screen.getByRole("menuitem", { name: resultLabel }).textContent,
      ).toBe(resultLabel);
      expect(h.get.mock.calls).toEqual([
        [ROW_URL, { params: { scope: "all", sort: "newest" } }],
      ]);
      const originalLocation = window.location.href;
      fireEvent.click(action());
      const dialog = await screen.findByRole("dialog", {
        name: "Run again in your code",
      });
      expect(within(dialog).getByText("Selected rows · #1").textContent).toBe(
        "Selected rows · #1",
      );
      const download = within(dialog).getByRole("button", {
        name: "Download handoff JSON",
      });
      await waitFor(() => expect(download.disabled).toBe(false));
      fireEvent.click(download);
      expect(downloads).toEqual([
        {
          sourceRunId: RUN,
          createUrl: "/model-hub/experiment-runs/",
          resultsUrl: `/model-hub/experiment-runs/${NEW_RUN}/results/`,
          body: {
            run_id: NEW_RUN,
            dataset_version_id: VERSION,
            experiment_id: experimentId,
            expected_items: [
              { example_id: EXAMPLE, trial_number: 2 },
              { example_id: EXAMPLE, trial_number: 7 },
            ],
            evaluator_metric_ids: ["metric-a"],
            model_config: { model: "fixture" },
            metadata: { code_revision: "revision-a" },
          },
          inputs: [pinnedInput],
          recipe: within(dialog).getByLabelText("REST recipe").value,
        },
      ]);
      expect(h.get.mock.calls.map(([url]) => url)).toEqual([
        ROW_URL,
        SOURCE_URL,
        VERSION_URL,
      ]);
      expect(h.uuid.mock.calls).toEqual([[]]);
      await waitFor(() =>
        expect(h.revoke.mock.calls).toEqual([["blob:handoff"]]),
      );
      expect(h.post.mock.calls).toEqual([]);
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(window.location.href).toBe(originalLocation);
      expect(h.post.mock.calls).toEqual([]);
    },
  );

  it.each(["prompt", "graph", "agent", "http"])(
    "does not offer the handoff for a %s run",
    async (targetType) => {
      await mount({ target_type: targetType });
      expect(
        screen.queryByRole("menuitem", { name: "Run again in your code…" }),
      ).toBeNull();
      expect(
        screen.getByRole("menuitem", { name: "Open this result" }).textContent,
      ).toBe("Open this result");
      if (targetType === "agent")
        expect(
          screen.getByRole("menuitem", { name: "Open the trajectory" })
            .textContent,
        ).toBe("Open the trajectory");
      expect(h.get.mock.calls.map(([url]) => url)).toEqual([ROW_URL]);
      expect(h.post.mock.calls).toEqual([]);
      expect(h.uuid.mock.calls).toEqual([]);
    },
  );

  it.each(["run", "download"])(
    "respects missing %s access",
    async (permission) => {
      h.canRun = permission !== "run";
      h.canDownload = permission !== "download";
      await mount();
      expect(action().getAttribute("aria-disabled")).toBe("true");
      fireEvent.click(action());
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(h.get.mock.calls.map(([url]) => url)).toEqual([ROW_URL]);
      expect(h.post.mock.calls).toEqual([]);
      expect(h.uuid.mock.calls).toEqual([]);
    },
  );

  it("does not substitute the dataset row for a missing run example", async () => {
    await mount({ example_id: null });
    expect(action().getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(action());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(h.get.mock.calls.map(([url]) => url)).toEqual([ROW_URL]);
    expect(h.uuid.mock.calls).toEqual([]);
    expect(h.post.mock.calls).toEqual([]);
  });
});
