// The New experiment wizard's read of the dataset version list in a launch
// build, as shipped (unreleased parts shown): one explicit page, its own
// cache entry next to the Data tab's list, and a caller that shows the
// failure itself can say so to the app's error toast. The switch-off case
// (the read is held and nothing is sent) is in honest-lines-held-field.
// Module mocks copied from honest-lines-held-field.test.jsx.
import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { datasetVersionsKey } from "../dataset-versions";
import { useExperimentDatasetVersions } from "../experiment-create";

// The show-unreleased switch as the product modules read it (shipped: true).
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

vi.mock(
  "src/sections/develop-detail/Experiment/RunExperimentFormSkeleton",
  () => ({
    default: () => <div data-testid="source-loading">Loading</div>,
  }),
);
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
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));
// Unused by these reads; avoid importing the grid and create-form UI.
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: vi.fn(),
  getDatasetQueryOptions: vi.fn(),
}));
vi.mock("src/sections/develop-detail/Experiment/useCreateAdmission", () => ({
  useCreateAdmission: vi.fn(),
}));

const DATASET = "0b5b7b9e-4444-4c8f-9a8e-0e2f8d1d2c3a";
const VERSIONS_URL = endpoints.develop.versions.list(DATASET);
// The server's own shape (dataset-version-list.json): no version number.
const SERVER_LIST = [
  {
    id: "0b5b7b9e-5555-4c8f-9a8e-0e2f8d1d2c3a",
    created_at: "2026-10-13T09:20:52.985855Z",
    description: "",
    metadata: { capture: "native-snapshot-v1", columns: {} },
    item_count: 5,
  },
];
const originalAdapter = client.defaults.adapter;
let queryClient;
let adapter;

/* eslint-disable react/prop-types */
function Wrapper({ children }) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
/* eslint-enable react/prop-types */

const versionCalls = () =>
  adapter.mock.calls.filter(([config]) => config.url === VERSIONS_URL);

beforeEach(() => {
  unreleased.shown = true;
  setLaunchOverrideForTest({ launch: true });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  adapter = vi.fn(async (config) => ({
    status: 200,
    statusText: "OK",
    headers: {},
    config,
    data: { status: true, result: { versions: SERVER_LIST, has_more: false } },
  }));
  client.defaults.adapter = adapter;
});
afterEach(() => {
  client.defaults.adapter = originalAdapter;
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
  queryClient.clear();
});

describe("the wizard's dataset version read, as shipped (unreleased parts shown)", () => {
  it("asks for one page of 100 versions", async () => {
    const { result } = renderHook(() => useExperimentDatasetVersions(DATASET), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.held).toBe(false);
    expect(result.current.data).toEqual(SERVER_LIST);
    expect(versionCalls()).toHaveLength(1);
    expect(versionCalls()[0][0].params).toEqual({ limit: 100 });
  });

  it("keeps its own cache entry: the Data tab's cached list is never read as the wizard's, nor replaced by it", async () => {
    const dataTabList = {
      data: { status: true, result: { versions: [{ id: "data-tab-only" }] } },
    };
    queryClient.setQueryData(datasetVersionsKey(DATASET), dataTabList);
    const { result } = renderHook(() => useExperimentDatasetVersions(DATASET), {
      wrapper: Wrapper,
    });
    expect(result.current.data).toBeUndefined();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(SERVER_LIST);
    expect(queryClient.getQueryData(datasetVersionsKey(DATASET))).toBe(
      dataTabList,
    );
  });

  it("is read again when the Data tab's version list is refreshed after a save", async () => {
    const { result } = renderHook(() => useExperimentDatasetVersions(DATASET), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(versionCalls()).toHaveLength(1);
    await queryClient.invalidateQueries({
      queryKey: datasetVersionsKey(DATASET),
    });
    await waitFor(() => expect(versionCalls()).toHaveLength(2));
  });

  it("carries the caller's meta, so a caller that shows the failure keeps the app's error toast out", async () => {
    adapter.mockRejectedValue(new Error("Network Error"));
    const { result } = renderHook(
      () =>
        useExperimentDatasetVersions(DATASET, {
          meta: { errorHandled: true },
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    const queries = queryClient
      .getQueryCache()
      .findAll({ queryKey: datasetVersionsKey(DATASET) });
    expect(queries).toHaveLength(1);
    expect(queries[0].options.meta).toEqual({ errorHandled: true });
  });
});
