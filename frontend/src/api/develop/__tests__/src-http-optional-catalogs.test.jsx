// SRC-HTTP-03: these are the metadata hooks mounted by a fresh HTTP draft.
// Keep the real hooks, endpoint builders, launch inventory and interceptor;
// only the transport adapter is replaced, so no request leaves the test.
import React from "react";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import {
  absentPathInterceptor,
  absentTemplateOf,
  absentTemplates,
} from "src/launch/absentPathGuard";
import {
  isLaunchBuild,
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { madeRealPathTemplates, madeRealSurfaceIds } from "src/launch/madeReal";
import {
  useExperimentDatasetVersions,
  useExperimentSampleTable,
} from "../experiment-create";
import { useRunConfigs } from "../r2";
import ExperimentSourceGate from "src/sections/develop-detail/Experiment/ExperimentSourceGate";
import { hydrateHttpSourceValues, serverExperimentBody, withHttpSourceSettings } from "src/sections/develop-detail/Experiment/serverBody";
import { experimentBodyOfValues } from "src/sections/develop-detail/Experiment/experimentBody";
import { emptyExperimentValues } from "src/sections/develop-detail/Experiment/experimentFormValues";
import { getExperimentDefaultValue } from "src/sections/develop-detail/Experiment/utils";

// The show-unreleased switch as the product modules read it. Its shipped
// value is true; a launch build installs the absent-path guard only with it
// off (installAbsentPathGuard), and the interceptor below does the same.
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

vi.mock("src/sections/develop-detail/Experiment/RunExperimentFormSkeleton", () => ({
  default: () => <div data-testid="source-loading">Loading</div>,
}));

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }));
// Unused by these reads; avoid importing the grid and create-form UI.
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: vi.fn(),
  getDatasetQueryOptions: vi.fn(),
}));
vi.mock("src/sections/develop-detail/Experiment/useCreateAdmission", () => ({
  useCreateAdmission: vi.fn(),
}));

const DATASET = "0b5b7b9e-4444-4c8f-9a8e-0e2f8d1d2c3a";
const VERSION = "0b5b7b9e-5555-4c8f-9a8e-0e2f8d1d2c3a";
const VERSION_TEMPLATE = "/model-hub/datasets/{dataset_id}/versions/";
const RUN_TEMPLATE = "/model-hub/datasets/{dataset_id}/runs/";
const VERSION_URL = endpoints.develop.versions.list(DATASET);
const RUN_URL = endpoints.develop.datasetRuns.list(DATASET);
const SAMPLE_URL = endpoints.develop.getDatasetDetail(DATASET);
const VERSIONS = [{ id: VERSION, number: 1, is_latest: true }];
const COLUMNS = [{ column_id: "question", name: "question", role: "input" }];
const ROWS = [{ id: "row-1", question: "Hello" }];
const SAMPLE = {
  column_config: COLUMNS,
  table: ROWS,
  metadata: { total_rows: 1, dataset_name: "HTTP inputs" },
};
const RUNS = {
  count: 0, offset: 0, has_more: false, next: null,
  baseline: null, metric_defs: [], results: [],
};

let queryClient;
let adapter;
let requests;
let interceptor;
let previousLog;
const originalAdapter = client.defaults.adapter;

/* eslint-disable react/prop-types */
function Wrapper({ children }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
/* eslint-enable react/prop-types */

function useCatalogReads(datasetId = DATASET, enabled = true) {
  return {
    versions: useExperimentDatasetVersions(datasetId, { enabled }),
    runs: useRunConfigs(datasetId, { enabled, retry: false }),
  };
}

function useHttpDraftReads(datasetId = DATASET, enabled = true) {
  const catalogs = useCatalogReads(datasetId, enabled);
  // ConfigureStepLLM mounts a second observer for baseline recommendations.
  const baseline = useRunConfigs(datasetId, { enabled, retry: false });
  const sample = useExperimentSampleTable(
    { datasetId, isLatest: true, splitIds: [] },
    { enabled },
  );
  return { ...catalogs, baseline, sample };
}

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  // Reproduce the staging source-surface claim without granting either path.
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: [...madeRealSurfaceIds(), "exp.new.runningSources"],
  });
  previousLog = window.__dsxLaunchAbsent;
  window.__dsxLaunchAbsent = [];
  requests = vi.fn();
  interceptor = client.interceptors.request.use((config) => {
    requests(config);
    return isLaunchBuild() && !unreleased.shown
      ? absentPathInterceptor(config)
      : config;
  });
  adapter = vi.fn(async (config) => {
    const results = {
      [VERSION_URL]: { versions: VERSIONS },
      [RUN_URL]: RUNS,
      [SAMPLE_URL]: SAMPLE,
    };
    if (!Object.hasOwn(results, config.url)) {
      throw new Error(`Unexpected test request: ${config.url}`);
    }
    return {
      status: 200, statusText: "OK", headers: {}, config,
      data: { status: true, result: results[config.url] },
    };
  });
  client.defaults.adapter = adapter;
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  client.interceptors.request.eject(interceptor);
  client.defaults.adapter = originalAdapter;
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
  if (previousLog === undefined) delete window.__dsxLaunchAbsent;
  else window.__dsxLaunchAbsent = previousLog;
});

const expectUnfetched = (query) => {
  expect(query.fetchStatus).toBe("idle");
  expect(query.isSuccess).toBe(false);
  expect(query.isError).toBe(false);
  expect(query.data).toBeUndefined();
};

describe("HTTP draft optional metadata admission", () => {
  it("loads current-row columns without sending either optional catalog to the guard", async () => {
    unreleased.shown = false;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: [...madeRealSurfaceIds(), "exp.new.runningSources"],
      showUnreleased: false,
    });
    expect(isSurfaceShown("exp.new")).toBe(true);
    expect(isSurfaceShown("exp.new.runningSources")).toBe(true);
    expect(absentTemplateOf(VERSION_URL)).toBe(VERSION_TEMPLATE);
    expect(absentTemplateOf(RUN_URL)).toBe(RUN_TEMPLATE);
    const { result } = renderHook(() => useHttpDraftReads(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.sample.isSuccess).toBe(true));
    expect(result.current.sample.data).toEqual({
      columns: COLUMNS, rows: ROWS, total: 1, datasetName: "HTTP inputs",
    });
    expectUnfetched(result.current.versions);
    await waitFor(() => {
      expect(result.current.runs.isError).toBe(true);
      expect(result.current.baseline.isError).toBe(true);
    });
    expect(result.current.runs.error.response.status).toBe(404);
    expect(result.current.runs.data).toBeUndefined();
    expect(requests.mock.calls.map(([config]) => config.url)).toEqual([SAMPLE_URL]);
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(window.__dsxLaunchAbsent).toEqual([]);

    // Shared-cache invalidation must not start a refused background read.
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["develop"] });
    });
    expect(requests).toHaveBeenCalledTimes(1);
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });

  it("as shipped (unreleased parts shown), reads the version list for the Data step; the run list stays refused locally", async () => {
    const { result } = renderHook(() => useHttpDraftReads(), {
      wrapper: Wrapper,
    });
    await waitFor(() => {
      expect(result.current.sample.isSuccess).toBe(true);
      expect(result.current.versions.isSuccess).toBe(true);
    });
    expect(result.current.versions.held).toBe(false);
    expect(result.current.versions.data).toEqual(VERSIONS);
    await waitFor(() => {
      expect(result.current.runs.isError).toBe(true);
      expect(result.current.baseline.isError).toBe(true);
    });
    expect(result.current.runs.error.response.status).toBe(404);
    expect(
      requests.mock.calls.map(([config]) => config.url).sort(),
    ).toEqual([SAMPLE_URL, VERSION_URL].sort());
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });

  it("locally rejects unavailable default-enabled run observers", async () => {
    const { result } = renderHook(() => useRunConfigs(DATASET), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error.response.status).toBe(404);
    expect(result.current.data).toBeUndefined();
    expect(requests).not.toHaveBeenCalled();
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });

  it("does not treat a backing claim as release of the explicit run-catalog hold", () => {
    const withClaims = absentTemplates(undefined, new Set([
      ...madeRealPathTemplates(), VERSION_TEMPLATE, RUN_TEMPLATE,
    ]));
    expect(withClaims).not.toContain(VERSION_TEMPLATE);
    expect(withClaims).toContain(RUN_TEMPLATE);
  });

  it.each([
    ["disabled", DATASET, false],
    ["missing dataset", null, true],
  ])("honors %s reads outside launch too", async (_label, datasetId, enabled) => {
    setLaunchOverrideForTest({ launch: false });
    const { result } = renderHook(() => useCatalogReads(datasetId, enabled), { wrapper: Wrapper });
    await act(async () => { await Promise.resolve(); });
    expectUnfetched(result.current.versions);
    expectUnfetched(result.current.runs);
    expect(requests).not.toHaveBeenCalled();
  });

  it("preserves version/history and baseline reads outside launch", async () => {
    setLaunchOverrideForTest({ launch: false });
    const { result } = renderHook(() => ({
      versions: useExperimentDatasetVersions(DATASET),
      runs: useRunConfigs(DATASET, { versionId: VERSION, retry: false }),
    }), { wrapper: Wrapper });
    await waitFor(() => {
      expect(result.current.versions.isSuccess).toBe(true);
      expect(result.current.runs.isSuccess).toBe(true);
    });
    expect(result.current.versions.data).toEqual(VERSIONS);
    expect(result.current.runs.data).toMatchObject(RUNS);
    expect(adapter.mock.calls.find(([config]) => config.url === RUN_URL)[0].params).toMatchObject({
      include: "metrics,config,lineage", dataset_version_id: VERSION, offset: 0,
    });
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });

  it("keeps admitted request failures as errors, not successful empty catalogs", async () => {
    setLaunchOverrideForTest({ launch: false });
    adapter.mockRejectedValue(new Error("Metadata unavailable"));
    const { result } = renderHook(() => useCatalogReads(), { wrapper: Wrapper });
    await waitFor(() => {
      expect(result.current.versions.isError).toBe(true);
      expect(result.current.runs.isError).toBe(true);
    });
    for (const query of [result.current.versions, result.current.runs]) {
      expect(query.isSuccess).toBe(false);
      expect(query.data).toBeUndefined();
      expect(query.error.message).toBe("Metadata unavailable");
    }
  });

  it.each([
    { status: false, result: { versions: [] } },
    { status: true, result: {} },
    { status: true, result: { versions: "unavailable" } },
  ])("does not turn a failed or malformed version envelope into empty history: %j", async (data) => {
    setLaunchOverrideForTest({ launch: false });
    adapter.mockImplementation(async (config) => ({
      status: 200, statusText: "OK", headers: {}, config, data,
    }));
    const { result } = renderHook(() => useExperimentDatasetVersions(DATASET), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isSuccess).toBe(false);
    expect(result.current.data).toBeUndefined();
  });

  it("still distinguishes a successful empty version catalog from an unfetched one", async () => {
    setLaunchOverrideForTest({ launch: false });
    adapter.mockImplementation(async (config) => ({
      status: 200, statusText: "OK", headers: {}, config,
      data: { status: true, result: { versions: [] } },
    }));
    const { result } = renderHook(() => useExperimentDatasetVersions(DATASET), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});


function RequiredRunSource() {
  const configs = useRunConfigs(DATASET);
  return (
    <ExperimentSourceGate detail={{ data: { id: "existing" } }} configs={configs}>
      <div>Editable source</div>
    </ExperimentSourceGate>
  );
}

it("cold-cache Edit and Duplicate terminate with unavailable instead of an endless skeleton", async () => {
  render(<RequiredRunSource />, { wrapper: Wrapper });
  await waitFor(() => expect(screen.getByText("Experiment unavailable")).toBeTruthy());
  expect(screen.queryByTestId("source-loading")).toBeNull();
  expect(screen.queryByText("Editable source")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry" }).disabled).toBe(false));
  expect(requests).not.toHaveBeenCalled();
  expect(adapter).not.toHaveBeenCalled();
  expect(window.__dsxLaunchAbsent).toEqual([]);
});


it("COR-01 samples and creates an HTTP duplicate within its stored split", async () => {
  const splitId = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51";
  const detail = { name: "Scoped HTTP", run_scope: { split_ids: [splitId] },
    prompt_configs: [], agent_configs: [], http_configs: [{ id: VERSION, order: 0,
      http: { url: "https://fixture.example/run", method: "POST", body_template: "{}", output_path: "$.reply" },
      variable_mapping: {},
    }],
  };
  const values = hydrateHttpSourceValues({ ...emptyExperimentValues(), ...getExperimentDefaultValue(detail) }, detail);
  const { result } = renderHook(() => useExperimentSampleTable({
    datasetId: DATASET, isLatest: true, splitIds: values.splitIds,
  }), { wrapper: Wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  const request = adapter.mock.calls.find(([config]) => config.url === SAMPLE_URL)[0];
  expect(JSON.parse(request.params.filters)).toEqual([{
    column_id: "system:splits", filter_config: {
      filter_type: "categorical", filter_op: "in", filter_value: [splitId],
    },
  }]);
  const wire = serverExperimentBody(withHttpSourceSettings(
    experimentBodyOfValues(values, { datasetId: DATASET }), values.promptConfig,
  ));
  expect(wire.blocks).toEqual([]);
  expect(wire.body.split_ids).toEqual([splitId]);
});
