/* eslint-disable react/prop-types */
// The side-by-side page reached by its address, on a build that uses the new
// experiment pages, where the server has no side-by-side read: the page shows
// the shared line and Back, mounts no workspace and asks for neither the task
// nor its review state. Where the read is served, the workspace mounts and
// asks as before. The workspace here stands in for the real one: it asks for
// the same two reads.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";

const h = vi.hoisted(() => ({ get: vi.fn(), mounts: 0 }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: h.get } };
});
vi.mock(
  "src/sections/experiment-detail-classic/dsxOnlyRoute",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useDsxOnlyCompareRedirect: () => null,
  }),
);
vi.mock("src/sections/task-compare/TaskCompareWorkspace", async () => {
  const { useTaskCompare, useTaskReview } = await import("src/api/develop/r3");
  return {
    default: function Workspace({ datasetId }) {
      h.mounts += 1;
      const args = { datasetId, rowId: "row-1", runIds: ["run-a", "run-b"] };
      useTaskCompare(args);
      useTaskReview(args);
      return <div data-testid="side-by-side-workspace" />;
    },
  };
});

import TaskCompare from "src/pages/dashboard/Develop/TaskCompare";

const DS = "d1d1d1d1-0000-4000-8000-000000000001";

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

const renderPage = (search = "?runs=run-a,run-b&row=row-1") => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <MemoryRouter
          initialEntries={[`/dashboard/develop/${DS}/compare/tasks${search}`]}
        >
          <Routes>
            <Route
              path="/dashboard/develop/:dataset/compare/tasks"
              element={<TaskCompare />}
            />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    </QueryClientProvider>,
  );
};

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

beforeEach(() => {
  h.get.mockReset();
  h.get.mockResolvedValue({ data: { status: true, result: { lanes: [] } } });
  h.mounts = 0;
});

afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
});

describe("side-by-side page where the server has no side-by-side read", () => {
  it("shows the shared line in place of the workspace", async () => {
    setRealBackendForTests(true);
    renderPage();
    await settle();
    expect(screen.getByText(NOT_IN_RELEASE)).toBeTruthy();
    expect(screen.queryByTestId("side-by-side-workspace")).toBeNull();
    expect(h.mounts).toBe(0);
    expect(screen.queryByText(/Couldn't load this task/)).toBeNull();
  });

  it("asks for neither the task nor its review state", async () => {
    setRealBackendForTests(true);
    renderPage();
    await settle();
    expect(compareCalls()).toHaveLength(0);
    expect(reviewCalls()).toHaveLength(0);
  });

  it("offers Back to where the link came from", async () => {
    setRealBackendForTests(true);
    const from = `/dashboard/develop/${DS}?tab=experiments`;
    renderPage(`?runs=run-a,run-b&from=${encodeURIComponent(from)}`);
    await settle();
    expect(
      screen.getByRole("link", { name: "Back" }).getAttribute("href"),
    ).toBe(from);
  });

  it.each([
    ["two slashes", "//example.com/runs"],
    ["a backslash", "/\\example.com/runs"],
    ["a tab", "/\t/example.com/runs"],
    ["a line break", "/\n/example.com/runs"],
  ])(
    "offers Back to the dataset page when the link names another site (%s)",
    async (_kind, from) => {
      setRealBackendForTests(true);
      renderPage(`?runs=run-a,run-b&from=${encodeURIComponent(from)}`);
      await settle();
      expect(
        screen.getByRole("link", { name: "Back" }).getAttribute("href"),
      ).toBe(`/dashboard/develop/${DS}`);
    },
  );

  it("offers Back to the dataset page when the link names nowhere", async () => {
    setRealBackendForTests(true);
    renderPage();
    await settle();
    expect(
      screen.getByRole("link", { name: "Back" }).getAttribute("href"),
    ).toBe(`/dashboard/develop/${DS}`);
  });
});

describe("side-by-side page where the read is served", () => {
  it("mounts the workspace, which asks as before", async () => {
    setRealBackendForTests(false);
    renderPage();
    expect(await screen.findByTestId("side-by-side-workspace")).toBeTruthy();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    await waitFor(() => expect(compareCalls()).toHaveLength(1));
    await waitFor(() => expect(reviewCalls()).toHaveLength(1));
  });
});
