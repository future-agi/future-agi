/* eslint-disable react/prop-types */
// L3 (the capability contract): the DSX compare pages (Compare runs, Side by side) mount
// only in prototype builds. A non-prototype build, experimentsBasic on or
// off, sends both deep links to the dataset page and never mounts them.
import React from "react";
import { render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mode = vi.hoisted(() => ({ prototype: true }));
const mounts = vi.hoisted(() => ({ compare: 0, tasks: 0, confirm: 0 }));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
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
  default: () => {
    mounts.tasks += 1;
    return <div data-testid="dsx-side-by-side" />;
  },
}));

import DatasetCompare from "src/pages/dashboard/Develop/DatasetCompare";
import TaskCompare from "src/pages/dashboard/Develop/TaskCompare";
import { compareFallbackPath } from "../dsxOnlyRoute";

const DS = "d1d1d1d1-0000-4000-8000-000000000001";

function DatasetPage() {
  const { pathname } = useLocation();
  return <div data-testid="dataset-page">{pathname}</div>;
}

// The two routes as routes/sections/dashboard.jsx declares them.
const renderAt = (path) =>
  render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
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

describe("L3 DSX compare pages", () => {
  beforeEach(() => {
    mounts.compare = 0;
    mounts.tasks = 0;
    mounts.confirm = 0;
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("falls back to the dataset page", () => {
    expect(compareFallbackPath(DS)).toBe(`/dashboard/develop/${DS}`);
    expect(compareFallbackPath(undefined)).toBe("/dashboard/develop");
  });

  it("prototype builds keep Compare runs and Side by side", async () => {
    mode.prototype = true;
    renderAt(`/dashboard/develop/${DS}/compare/data`);
    expect(await screen.findByTestId("dsx-compare-runs")).toBeTruthy();
    renderAt(`/dashboard/develop/${DS}/compare/tasks`);
    expect(await screen.findByTestId("dsx-side-by-side")).toBeTruthy();
  });

  for (const basic of ["true", "false"]) {
    describe(`non-prototype build, experimentsBasic ${basic}`, () => {
      beforeEach(() => {
        mode.prototype = false;
        vi.stubEnv("VITE_DSX_EXPERIMENTS_BASIC", basic);
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
        });
      }

      it("tasks: lands on the dataset page, Side by side never mounts", async () => {
        renderAt(`/dashboard/develop/${DS}/compare/tasks`);
        expect((await screen.findByTestId("dataset-page")).textContent).toBe(
          `/dashboard/develop/${DS}`,
        );
        expect(mounts.tasks).toBe(0);
        expect(screen.queryByTestId("dsx-side-by-side")).toBeNull();
      });
    });
  }
});
