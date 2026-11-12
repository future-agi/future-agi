/* eslint-disable react/prop-types */
// L3 (the capability contract): which experiment page a build mounts.
// Prototype -> DSX; non-prototype -> classic with the default ON or explicit
// experimentsBasic=false build input. The flag gates NEW surfaces, not classic.
// Both switch states are pinned the way the launch tests pin them: with
// unreleased parts hidden the cases above hold as written; as shipped
// (unreleased parts shown) a non-prototype build reads the experiment's
// detail and opens the classic page for an experiment the DSX page cannot
// show in full, and the DSX page otherwise.
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
const mode = vi.hoisted(() => ({ prototype: true }));
const mounts = vi.hoisted(() => ({ dsx: 0, classic: 0 }));
const requests = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: requests,
}));
vi.mock("src/sections/experiment-detail/ExperimentWrapperView", () => ({
  default: () => {
    mounts.dsx += 1;
    return <div data-testid="dsx-experiment-page" />;
  },
}));
vi.mock("src/sections/experiment-detail-classic/ExperimentWrapperView", () => ({
  default: () => {
    mounts.classic += 1;
    return <div data-testid="classic-experiment-page" />;
  },
}));

import ExperimentWrapper from "src/pages/dashboard/Develop/ExperimentWrapper";
import {
  experimentSurfaceOf,
  experimentsBasicFromEnv,
} from "src/sections/experiment-detail-classic/experimentsBasic";

// Switch off: the override launchSwitch reads and the export the product
// modules read both say unreleased parts are hidden.
const switchOff = (state) =>
  setLaunchOverrideForTest({ ...state, showUnreleased: false });

const detailOf = (full) => ({
  data: {
    status: true,
    result: {
      id: "e1",
      dataset_id: "d1",
      name: "Experiment",
      runs: full ? [{ run_id: "r1", experiment_id: "e1" }] : [],
      runs_total: full ? 1 : 0,
      runs_has_more: false,
      runs_next: null,
      results_availability: {
        reason: full ? null : "native_identity_not_recorded",
      },
      opens_in_full_results: full,
    },
  },
});

let client;
const renderAt = (path) => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/dashboard/develop"
              element={<div>datasets list</div>}
            />
            <Route
              path="/dashboard/develop/experiment/:experimentId/*"
              element={<ExperimentWrapper />}
            />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    </QueryClientProvider>,
  );
};

describe("L3 experiment page switch", () => {
  beforeEach(() => {
    mounts.dsx = 0;
    mounts.classic = 0;
    unreleased.shown = false;
    requests.get.mockReset();
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    client?.clear();
  });

  it("maps prototype / experimentsBasic to one surface", () => {
    expect(experimentSurfaceOf({ prototype: true, basic: false })).toBe("dsx");
    expect(experimentSurfaceOf({ prototype: true, basic: true })).toBe("dsx");
    expect(experimentSurfaceOf({ prototype: false, basic: true })).toBe(
      "classic",
    );
    expect(experimentSurfaceOf({ prototype: false, basic: false })).toBe(
      "classic",
    );
  });

  it("defaults experimentsBasic ON and disables it only for the literal false build input", () => {
    expect(experimentsBasicFromEnv({})).toBe(true);
    for (const value of [undefined, "", "1", "TRUE", "False", "true"])
      expect(
        experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: value }),
        String(value),
      ).toBe(true);
    expect(
      experimentsBasicFromEnv({ VITE_DSX_EXPERIMENTS_BASIC: "false" }),
    ).toBe(false);
  });

  it("prototype mode keeps the DSX experiment page", async () => {
    mode.prototype = true;
    switchOff({ launch: true, experimentsBasic: true });
    renderAt("/dashboard/develop/experiment/e1/data");
    expect(await screen.findByTestId("dsx-experiment-page")).toBeTruthy();
    expect(mounts.classic).toBe(0);
  });

  it("a non-prototype build with no experimentsBasic input mounts the classic page, never DSX", async () => {
    mode.prototype = false;
    switchOff({
      launch: true,
      experimentsBasic: experimentsBasicFromEnv({}),
    });
    renderAt("/dashboard/develop/experiment/e1/summary");
    expect(await screen.findByTestId("classic-experiment-page")).toBeTruthy();
    expect(mounts.dsx).toBe(0);
    // With unreleased parts hidden the page is chosen without a read.
    expect(requests.get).not.toHaveBeenCalled();
  });

  // Classic Data and Summary remain with explicit false;
  // neither mounting a classic host nor the ON default activates the new Results view.
  it.each(["data", "summary"])(
    "an explicit false non-prototype build keeps the classic %s page visible",
    async (tab) => {
      mode.prototype = false;
      const experimentsBasic = experimentsBasicFromEnv({
        VITE_DSX_EXPERIMENTS_BASIC: "false",
      });
      expect(experimentsBasic).toBe(false);
      switchOff({ launch: true, experimentsBasic });
      expect(isSurfaceShown("exp.classic")).toBe(true);
      expect(isSurfaceShown("exp.new")).toBe(false);
      for (const id of [
        "exp.results.matrix",
        "exp.results.paths",
        "exp.results.summary",
        "exp.results.traces",
      ])
        expect(isSurfaceShown(id), id).toBe(false);
      renderAt(`/dashboard/develop/experiment/e1/${tab}`);
      expect(
        await screen.findByTestId("classic-experiment-page"),
      ).toBeVisible();
      expect(screen.queryByText("datasets list")).toBeNull();
      expect(mounts.dsx).toBe(0);
      expect(mounts.classic).toBeGreaterThan(0);
    },
  );
});

describe("experiment page switch as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    mounts.dsx = 0;
    mounts.classic = 0;
    mode.prototype = false;
    unreleased.shown = true;
    requests.get.mockReset();
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    client?.clear();
    unreleased.shown = false;
  });

  it.each(["data", "summary"])(
    "opens an experiment the DSX page cannot show in full on the classic %s page",
    async (tab) => {
      requests.get.mockResolvedValue(detailOf(false));
      renderAt(`/dashboard/develop/experiment/e1/${tab}`);
      const page = await screen.findByTestId("classic-experiment-page");
      expect(page).toBeVisible();
      expect(mounts.dsx).toBe(0);
      expect(isSurfaceShown("exp.classic")).toBe(true);
      await waitFor(() => expect(requests.get).toHaveBeenCalledTimes(1));
    },
  );

  it("opens an experiment the DSX page can show in full on the DSX page", async () => {
    requests.get.mockResolvedValue(detailOf(true));
    renderAt("/dashboard/develop/experiment/e1/data");
    expect(await screen.findByTestId("dsx-experiment-page")).toBeVisible();
    expect(mounts.classic).toBe(0);
  });

  it("keeps prototype mode on the DSX page without reading the experiment first", async () => {
    mode.prototype = true;
    requests.get.mockResolvedValue(detailOf(false));
    renderAt("/dashboard/develop/experiment/e1/data");
    expect(await screen.findByTestId("dsx-experiment-page")).toBeVisible();
    expect(mounts.classic).toBe(0);
    expect(requests.get).not.toHaveBeenCalled();
  });
});
