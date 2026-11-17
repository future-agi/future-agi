/* eslint-disable react/prop-types */
// The side-by-side task read and its review state where the server has no
// route for them: neither hook asks for anything. Where they are served,
// both ask as before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { useTaskCompare, useTaskReview } from "../r3";

const h = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: h.get } };
});

const DS = "d1d1d1d1-0000-4000-8000-000000000001";
const ARGS = { datasetId: DS, rowId: "row-1", runIds: ["run-a", "run-b"] };

const compareCalls = () =>
  h.get.mock.calls.filter(
    ([url]) =>
      String(url).includes("/task-compare/") &&
      !String(url).includes("/task-compare/review/"),
  );
const reviewCalls = () =>
  h.get.mock.calls.filter(([url]) =>
    String(url).includes("/task-compare/review/"),
  );

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

// Lets any request the hooks would start go out before the check.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

beforeEach(() => {
  h.get.mockReset();
  h.get.mockResolvedValue({ data: { status: true, result: { lanes: [] } } });
});

afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
});

describe("side-by-side reads where the server has no route for them", () => {
  it("useTaskCompare asks for nothing", async () => {
    setRealBackendForTests(true);
    const { result } = renderWithClient(() => useTaskCompare(ARGS));
    await settle();
    expect(compareCalls()).toHaveLength(0);
    expect(result.current.fetchStatus).toBe("idle");
    expect(result.current.isError).toBe(false);
  });

  it("useTaskReview asks for nothing", async () => {
    setRealBackendForTests(true);
    const { result } = renderWithClient(() => useTaskReview(ARGS));
    await settle();
    expect(reviewCalls()).toHaveLength(0);
    expect(result.current.fetchStatus).toBe("idle");
    expect(result.current.isError).toBe(false);
  });
});

describe("side-by-side reads where the server serves them", () => {
  it("useTaskCompare asks for the task as before", async () => {
    setRealBackendForTests(false);
    renderWithClient(() => useTaskCompare(ARGS));
    await waitFor(() => expect(compareCalls()).toHaveLength(1));
    const [url, config] = compareCalls()[0];
    expect(url).toBe(`/model-hub/datasets/${DS}/task-compare/`);
    expect(config.params).toMatchObject({ row: "row-1", runs: "run-a,run-b" });
  });

  it("useTaskReview asks for the review state as before", async () => {
    setRealBackendForTests(false);
    renderWithClient(() => useTaskReview(ARGS));
    await waitFor(() => expect(reviewCalls()).toHaveLength(1));
    expect(reviewCalls()[0][0]).toBe(
      `/model-hub/datasets/${DS}/task-compare/review/`,
    );
  });
});
