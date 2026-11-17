/* eslint-disable react/prop-types */
// Compare routes select DSX for prototype OR show-unreleased, independently of
// experimentsBasic. The explicit hidden profile still redirects without mounting.
// Visibility alone does not enable the separate side-by-side task read.
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";

const mode = vi.hoisted(() => ({ prototype: false, showUnreleased: false }));
const mounts = vi.hoisted(() => ({ compare: 0, tasks: 0, confirm: 0 }));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
}));
// Vary only the build profile; keep the real launch helpers, surface selector
// and redirect hook. An env stub alone cannot change this committed export.
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  get SHOW_UNRELEASED_PARTS() {
    return mode.showUnreleased;
  },
}));
vi.mock("src/sections/experiment-detail/CompareWrapperView", () => ({
  default: ({ tab }) => {
    mounts.compare += 1;
    return <div data-testid="dsx-compare-runs">{tab}</div>;
  },
}));
vi.mock("src/sections/develop-detail/Experiment/RunActionConfirmHost", () => ({
  default: () => {
    mounts.confirm += 1;
    return null;
  },
}));
vi.mock("src/sections/task-compare/TaskCompareWorkspace", () => ({
  default: ({ datasetId }) => {
    mounts.tasks += 1;
    return <div data-testid="dsx-side-by-side">{datasetId}</div>;
  },
}));

import DatasetCompare from "src/pages/dashboard/Develop/DatasetCompare";
import TaskCompare from "src/pages/dashboard/Develop/TaskCompare";
import { taskCompareServed } from "src/api/develop/taskCompareServed";
import { compareFallbackPath } from "../dsxOnlyRoute";
import { isExperimentsBasicOn } from "../experimentsBasic";

const DS = "d1d1d1d1-0000-4000-8000-000000000001";
const SELECTION = "?runs=run-a,run-b&baseline=run-a&row=row-1&run=run-a";

function DatasetPage() {
  const { pathname } = useLocation();
  return <div data-testid="dataset-page">{pathname}</div>;
}

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="route-location">{pathname + search}</div>;
}

// Production has CompareIndexRedirect as its index child. This deliberately
// bounded stand-in proves the admitted parent's Outlet renders its index child;
// it does NOT exercise default-tab selection, run reads or mixed-kind loading.
function CompareIndexOutletFixture() {
  return <div data-testid="compare-index-outlet" />;
}

// Keep the real page/child nesting and separate task route from dashboard.jsx.
// Only the index redirect and leaf workspaces are represented by fixtures.
const renderAt = (path) =>
  render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <Routes>
          <Route path="/dashboard/develop/:dataset" element={<DatasetPage />} />
          <Route
            path="/dashboard/develop/:dataset/compare/tasks"
            element={<TaskCompare />}
          />
          <Route
            path="/dashboard/develop/:dataset/compare"
            element={<DatasetCompare />}
          >
            <Route index element={<CompareIndexOutletFixture />} />
            <Route path="data" element={null} />
            <Route path="matrix" element={null} />
            <Route path="paths" element={null} />
            <Route path="summary" element={null} />
          </Route>
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );

const COMPARE_LINKS = [
  `/dashboard/develop/${DS}/compare`,
  `/dashboard/develop/${DS}/compare/data`,
  `/dashboard/develop/${DS}/compare/matrix`,
  `/dashboard/develop/${DS}/compare/paths`,
  `/dashboard/develop/${DS}/compare/summary`,
];

function setBasicProfile(basic) {
  vi.stubEnv("VITE_EXPERIMENTS_NEW_PAGES", basic);
  // launchSwitch snapshots env at import time; exercise both real helper
  // values as well as the env input, rather than silently keeping its default.
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: basic === "true",
  });
  expect(isExperimentsBasicOn()).toBe(basic === "true");
}

describe("L3 DSX compare pages", () => {
  beforeEach(() => {
    mode.prototype = false;
    mode.showUnreleased = false;
    mounts.compare = 0;
    mounts.tasks = 0;
    mounts.confirm = 0;
    setBasicProfile("false");
    // The real capability helper serves reads in the non-real/test profile.
    // Unavailable-read cases below explicitly select the real backend instead.
    setRealBackendForTests(false);
  });
  afterEach(() => {
    cleanup();
    mode.prototype = false;
    mode.showUnreleased = false;
    mounts.compare = 0;
    mounts.tasks = 0;
    mounts.confirm = 0;
    setLaunchOverrideForTest(null);
    setRealBackendForTests(null);
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  it("falls back to the dataset page", () => {
    expect(compareFallbackPath(DS)).toBe(`/dashboard/develop/${DS}`);
    expect(compareFallbackPath(undefined)).toBe("/dashboard/develop");
  });

  it.each([false, true])(
    "prototype builds keep Compare runs and Side by side with showUnreleased %s",
    async (showUnreleased) => {
      mode.prototype = true;
      mode.showUnreleased = showUnreleased;
      expect(taskCompareServed()).toBe(true);
      const compare = renderAt(`/dashboard/develop/${DS}/compare/data`);
      expect(await screen.findByTestId("dsx-compare-runs")).toBeTruthy();
      expect(mounts.confirm).toBeGreaterThan(0);
      compare.unmount();
      renderAt(`/dashboard/develop/${DS}/compare/tasks`);
      expect(await screen.findByTestId("dsx-side-by-side")).toBeTruthy();
      expect(screen.queryByTestId("dataset-page")).toBeNull();
    },
  );

  for (const basic of ["true", "false"]) {
    describe(`hidden non-prototype build, showUnreleased false, experimentsBasic ${basic}`, () => {
      beforeEach(() => {
        mode.prototype = false;
        mode.showUnreleased = false;
        setBasicProfile(basic);
      });

      for (const link of COMPARE_LINKS) {
        it(`${link.split("/compare")[1] || "(index)"}: lands on the dataset page, Compare runs never mounts`, async () => {
          renderAt(link);
          expect((await screen.findByTestId("dataset-page")).textContent).toBe(
            `/dashboard/develop/${DS}`,
          );
          expect(mounts.compare).toBe(0);
          expect(mounts.confirm).toBe(0);
          expect(screen.queryByTestId("dsx-compare-runs")).toBeNull();
          expect(screen.queryByTestId("compare-index-outlet")).toBeNull();
        });
      }

      it("tasks: lands on the dataset page, Side by side never mounts", async () => {
        renderAt(`/dashboard/develop/${DS}/compare/tasks`);
        expect((await screen.findByTestId("dataset-page")).textContent).toBe(
          `/dashboard/develop/${DS}`,
        );
        expect(mounts.tasks).toBe(0);
        expect(screen.queryByTestId("dsx-side-by-side")).toBeNull();
        expect(screen.queryByTestId("task-compare-not-in-release")).toBeNull();
      });
    });

    describe(`visible non-prototype build, showUnreleased true, experimentsBasic ${basic}`, () => {
      beforeEach(() => {
        mode.prototype = false;
        mode.showUnreleased = true;
        setBasicProfile(basic);
      });

      it("(index): admits the bounded index child through Outlet and keeps the query", async () => {
        const link = `/dashboard/develop/${DS}/compare${SELECTION}`;
        renderAt(link);
        expect(await screen.findByTestId("compare-index-outlet")).toBeTruthy();
        expect(screen.getByTestId("route-location").textContent).toBe(link);
        expect(screen.queryByTestId("dataset-page")).toBeNull();
        expect(screen.queryByTestId("dsx-compare-runs")).toBeNull();
        expect(mounts.compare).toBe(0);
        expect(mounts.confirm).toBe(0);
      });

      for (const tab of ["data", "matrix", "paths", "summary"]) {
        it(`${tab}: mounts the requested Compare tab and keeps the selection query`, async () => {
          const link = `/dashboard/develop/${DS}/compare/${tab}${SELECTION}`;
          renderAt(link);
          expect(
            (await screen.findByTestId("dsx-compare-runs")).textContent,
          ).toBe(tab);
          expect(mounts.compare).toBeGreaterThan(0);
          expect(mounts.confirm).toBeGreaterThan(0);
          expect(mounts.tasks).toBe(0);
          expect(screen.getByTestId("route-location").textContent).toBe(link);
          expect(screen.queryByTestId("dataset-page")).toBeNull();
          expect(screen.queryByTestId("compare-index-outlet")).toBeNull();
        });
      }

      it("tasks: mounts the served workspace without Compare tabs and keeps the query", async () => {
        expect(taskCompareServed()).toBe(true);
        const link = `/dashboard/develop/${DS}/compare/tasks${SELECTION}`;
        renderAt(link);
        expect(
          (await screen.findByTestId("dsx-side-by-side")).textContent,
        ).toBe(DS);
        expect(mounts.tasks).toBeGreaterThan(0);
        expect(mounts.compare).toBe(0);
        expect(mounts.confirm).toBe(0);
        expect(screen.getByTestId("route-location").textContent).toBe(link);
        expect(screen.queryByTestId("dataset-page")).toBeNull();
        expect(screen.queryByTestId("dsx-compare-runs")).toBeNull();
        expect(screen.queryByTestId("task-compare-not-in-release")).toBeNull();
      });

      it.each([false, true])(
        "tasks: unserved read shows the calm line and Back without mounting (from supplied: %s)",
        async (withFrom) => {
          setRealBackendForTests(true);
          expect(taskCompareServed()).toBe(false);
          const from = `/dashboard/develop/${DS}?tab=experiments`;
          const link = `/dashboard/develop/${DS}/compare/tasks${SELECTION}${
            withFrom ? `&from=${encodeURIComponent(from)}` : ""
          }`;
          renderAt(link);
          expect(
            await screen.findByTestId("task-compare-not-in-release"),
          ).toBeTruthy();
          expect(screen.getByText(NOT_IN_RELEASE)).toBeTruthy();
          expect(
            screen.getByRole("link", { name: "Back" }).getAttribute("href"),
          ).toBe(withFrom ? from : `/dashboard/develop/${DS}`);
          expect(screen.getByTestId("route-location").textContent).toBe(link);
          expect(mounts.tasks).toBe(0);
          expect(mounts.compare).toBe(0);
          expect(mounts.confirm).toBe(0);
          expect(screen.queryByTestId("dsx-side-by-side")).toBeNull();
          expect(screen.queryByTestId("dataset-page")).toBeNull();
          expect(screen.queryByText(/Couldn't load this task/)).toBeNull();
        },
      );
    });
  }
});
