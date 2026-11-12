// Honest lines rows 21 + 22: the one shared signal views use to say "Not
// available in this release". useExperimentDatasetVersions and useRunConfigs
// report `held: true` when this build refuses their path (the absent-path
// guard's own absentTemplateOf + the launch hold list), and send nothing.
// Module mocks copied from src-http-optional-catalogs.test.jsx.
import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  useExperimentDatasetVersions,
  wizardVersionsOf,
} from "../experiment-create";
import { useRunConfigs } from "../r2";

// The show-unreleased switch as the product modules read it. Its shipped
// value is true; the launch-build refusal below is the switch-off case, set
// in the override and in this flag together.
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
const originalAdapter = client.defaults.adapter;
let queryClient;
let adapter;

/* eslint-disable react/prop-types */
function Wrapper({ children }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
/* eslint-enable react/prop-types */

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  adapter = vi.fn(async (config) => ({
    status: 200, statusText: "OK", headers: {}, config,
    data: { status: true, result: { versions: [], results: [], count: 0 } },
  }));
  client.defaults.adapter = adapter;
});
afterEach(() => {
  client.defaults.adapter = originalAdapter;
  setLaunchOverrideForTest(null);
  unreleased.shown = true;
  queryClient.clear();
});

describe("held: the build refuses the read", () => {
  it("versions and run list report held in a launch build and send nothing", async () => {
    unreleased.shown = false;
    setLaunchOverrideForTest({ launch: true, showUnreleased: false });
    const versions = renderHook(() => useExperimentDatasetVersions(DATASET), { wrapper: Wrapper });
    const runs = renderHook(() => useRunConfigs(DATASET), { wrapper: Wrapper });
    expect(versions.result.current.held).toBe(true);
    expect(versions.result.current.fetchStatus).toBe("idle");
    expect(runs.result.current.held).toBe(true);
    await waitFor(() => expect(runs.result.current.isError).toBe(true));
    expect(adapter).not.toHaveBeenCalled();
  });

  it("as shipped (unreleased parts shown), a launch build reads the version list; the run list stays held", async () => {
    setLaunchOverrideForTest({ launch: true });
    const versions = renderHook(() => useExperimentDatasetVersions(DATASET), {
      wrapper: Wrapper,
    });
    const runs = renderHook(() => useRunConfigs(DATASET), { wrapper: Wrapper });
    expect(versions.result.current.held).toBe(false);
    expect(runs.result.current.held).toBe(true);
    await waitFor(() => expect(versions.result.current.isSuccess).toBe(true));
    expect(versions.result.current.data).toEqual([]);
    await waitFor(() => expect(runs.result.current.isError).toBe(true));
    expect(adapter.mock.calls.map(([config]) => config.url)).toEqual([
      endpoints.develop.versions.list(DATASET),
    ]);
  });

  it("reports not held outside a launch build", () => {
    setLaunchOverrideForTest({ launch: false });
    const versions = renderHook(() => useExperimentDatasetVersions(DATASET), { wrapper: Wrapper });
    const runs = renderHook(() => useRunConfigs(DATASET), { wrapper: Wrapper });
    expect(versions.result.current.held).toBe(false);
    expect(runs.result.current.held).toBe(false);
  });

  it("is not held without a dataset", () => {
    setLaunchOverrideForTest({ launch: true });
    const versions = renderHook(() => useExperimentDatasetVersions(undefined), { wrapper: Wrapper });
    const runs = renderHook(() => useRunConfigs(undefined), { wrapper: Wrapper });
    expect(versions.result.current.held).toBe(false);
    expect(runs.result.current.held).toBe(false);
  });
});

describe("the version lists the New experiment wizard reads", () => {
  // The server's own shape: no version number (dataset-version-list.json).
  const LIST = [
    {
      id: "0b5b7b9e-5555-4c8f-9a8e-0e2f8d1d2c3a",
      created_at: "2026-10-13T09:20:52.985855Z",
      description: "",
      item_count: 5,
    },
  ];

  it("a launch build runs the current rows and lists saved versions only for the Data step", () => {
    expect(wizardVersionsOf(LIST, true)).toEqual({
      versions: [],
      savedVersions: LIST,
      runsCurrentRows: true,
    });
  });

  it("outside a launch build the list stays the version list", () => {
    expect(wizardVersionsOf(LIST, false)).toEqual({
      versions: LIST,
      savedVersions: [],
      runsCurrentRows: false,
    });
  });

  it("reads the build when not told and treats a missing list as empty", () => {
    setLaunchOverrideForTest({ launch: true });
    expect(wizardVersionsOf(undefined)).toEqual({
      versions: [],
      savedVersions: [],
      runsCurrentRows: true,
    });
    setLaunchOverrideForTest({ launch: false });
    expect(wizardVersionsOf(undefined)).toEqual({
      versions: [],
      savedVersions: [],
      runsCurrentRows: false,
    });
  });
});
