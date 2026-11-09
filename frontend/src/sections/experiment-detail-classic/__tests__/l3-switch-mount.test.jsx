/* eslint-disable react/prop-types */
// L3 (the capability contract): which experiment page a build mounts.
// Prototype -> DSX; non-prototype -> classic with the default ON or explicit
// experimentsBasic=false build input. The flag gates NEW surfaces, not classic.
import React from "react";
import { render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const mode = vi.hoisted(() => ({ prototype: true }));
const mounts = vi.hoisted(() => ({ dsx: 0, classic: 0 }));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
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

const renderAt = (path) =>
  render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/dashboard/develop" element={<div>datasets list</div>} />
          <Route
            path="/dashboard/develop/experiment/:experimentId/*"
            element={<ExperimentWrapper />}
          />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );

describe("L3 experiment page switch", () => {
  beforeEach(() => {
    mounts.dsx = 0;
    mounts.classic = 0;
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
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
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
    renderAt("/dashboard/develop/experiment/e1/data");
    expect(await screen.findByTestId("dsx-experiment-page")).toBeTruthy();
    expect(mounts.classic).toBe(0);
  });

  it("a non-prototype build with no experimentsBasic input mounts the classic page, never DSX", async () => {
    mode.prototype = false;
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: experimentsBasicFromEnv({}),
    });
    renderAt("/dashboard/develop/experiment/e1/summary");
    expect(await screen.findByTestId("classic-experiment-page")).toBeTruthy();
    expect(mounts.dsx).toBe(0);
  });

  // D2 / J09 REVISED: classic Data and Summary remain with explicit false;
  // neither mounting a classic host nor the ON default activates DSX Results.
  it.each(["data", "summary"])(
    "an explicit false non-prototype build keeps the classic %s page visible",
    async (tab) => {
      mode.prototype = false;
      const experimentsBasic = experimentsBasicFromEnv({
        VITE_DSX_EXPERIMENTS_BASIC: "false",
      });
      expect(experimentsBasic).toBe(false);
      setLaunchOverrideForTest({ launch: true, experimentsBasic });
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
