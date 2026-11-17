/* eslint-disable react/prop-types */
// The result drawer's Trajectory tab reads its per-run marks through
// useRunMarkers. Where the server has no side-by-side read, the marks ask
// for nothing and are simply empty: nothing waits, nothing fails, and the
// tab draws the steps it has without them. Where the read is served, the
// marks come from it as before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import useRunMarkers from "../useRunMarkers";

const h = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: h.get } };
});

const DS = "d1d1d1d1-0000-4000-8000-000000000001";
const ARGS = { datasetId: DS, rowId: "row-1", runIds: ["run-a"] };
const PAYLOAD = {
  lanes: [
    {
      run_id: "run-a",
      label: "#1A",
      kind: "prompt",
      trials: [
        { trial: 1, outcome: "pass", steps: [] },
        { trial: 2, outcome: "fail", steps: [] },
      ],
    },
  ],
};

const taskCompareCalls = () =>
  h.get.mock.calls.filter(([url]) => String(url).includes("/task-compare/"));

const renderWithClient = (hook) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderHook(hook, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
};

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

beforeEach(() => {
  h.get.mockReset();
  h.get.mockResolvedValue({ data: { status: true, result: PAYLOAD } });
});

afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
});

describe("Trajectory marks where the side-by-side read has no route", () => {
  it("sends no task-compare request", async () => {
    setRealBackendForTests(true);
    renderWithClient(() => useRunMarkers(ARGS, { enabled: true }));
    await settle();
    expect(taskCompareCalls()).toHaveLength(0);
  });

  it("does not wait and gives no marks, so the steps show without them", async () => {
    setRealBackendForTests(true);
    const { result } = renderWithClient(() =>
      useRunMarkers(ARGS, { enabled: true }),
    );
    await settle();
    expect(result.current.isLoading).toBe(false);
    expect(result.current.byRun).toEqual({});
    expect(result.current.marksOf("run-a", 1)).toBeNull();
    expect(result.current.traceOf("run-a", 1)).toBeNull();
  });
});

describe("Trajectory marks where the side-by-side read is served", () => {
  it("reads the marks from it as before", async () => {
    setRealBackendForTests(false);
    const { result } = renderWithClient(() =>
      useRunMarkers(ARGS, { enabled: true }),
    );
    await waitFor(() =>
      expect(result.current.byRun["run-a"]?.firstFailed).toBe(2),
    );
    expect(taskCompareCalls()).toHaveLength(1);
  });
});
