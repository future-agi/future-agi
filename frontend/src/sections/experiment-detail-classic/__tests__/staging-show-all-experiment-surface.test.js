import { createElement } from "react";
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { Navigate } from "react-router";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockBackend = vi.hoisted(() => ({ loaded: vi.fn(), start: vi.fn() }));

vi.mock("src/_mock/api/browser", () => {
  mockBackend.loaded();
  return { worker: { start: mockBackend.start } };
});

// A staging build opens each experiment by its own detail read
// (opens_in_full_results): one the DSX page can show in full on the DSX page,
// one created before runs were recorded on the classic page. Only that read
// reaches the API here.
const requests = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: requests,
}));
const detailOf = (marker) =>
  marker === "before-runs"
    ? {
        id: "e1",
        dataset_id: "d1",
        name: "Earlier experiment",
        runs: [],
        runs_total: 0,
        runs_has_more: false,
        runs_next: null,
        results_availability: {
          reason: "native_identity_not_recorded",
          classic_url: "/dashboard/develop/experiment/e1/data",
        },
        opens_in_full_results: false,
      }
    : {
        id: "e1",
        dataset_id: "d1",
        name: "Experiment with runs",
        runs: [{ run_id: "r1", experiment_id: "e1" }],
        runs_total: 1,
        runs_has_more: false,
        runs_next: null,
        results_availability: {
          reason: null,
          classic_url: "/dashboard/develop/experiment/e1/data",
        },
        opens_in_full_results: true,
      };

/* eslint-disable react/prop-types */
vi.mock("src/sections/experiment-detail/ExperimentWrapperView", () => ({
  default: () => createElement("div", { "data-testid": "dsx-experiment-page" }),
}));
vi.mock("src/sections/experiment-detail-classic/ExperimentWrapperView", () => ({
  default: () => createElement("div", { "data-testid": "classic-experiment-page" }),
}));

function CompareRoute({ children }) {
  return createElement(
    MemoryRouter,
    { initialEntries: ["/dashboard/develop/d1/compare/data?runs=A,B"] },
    createElement(
      Routes,
      null,
      createElement(Route, {
        path: "/dashboard/develop/:dataset/compare/data",
        element: children,
      }),
    ),
  );
}
/* eslint-enable react/prop-types */

// Use the real prototype helper. Only the show-unreleased export varies;
// undefined models a launch module that does not export that switch at all.
async function load({ prototype = false, showUnreleased } = {}) {
  vi.resetModules();
  vi.stubEnv("MODE", "production");
  vi.stubEnv("VITE_ENVIRONMENT", prototype ? "local" : "staging");
  vi.stubEnv("VITE_PROTOTYPE_MODE", String(prototype));
  vi.stubEnv("VITE_ENABLE_MSW", "false");
  vi.doMock("src/launch/launchSwitch", async (importOriginal) => {
    const { SHOW_UNRELEASED_PARTS: _showUnreleased, ...launch } =
      await importOriginal();
    return showUnreleased === undefined
      ? launch
      : { ...launch, SHOW_UNRELEASED_PARTS: showUnreleased };
  });
  const prototypeModule = await import("src/utils/prototype");
  const selector = await import("../experimentsBasic");
  const compare = await import("../dsxOnlyRoute");
  return { prototypeModule, ...selector, ...compare };
}

afterEach(() => {
  cleanup();
  vi.doUnmock("src/launch/launchSwitch");
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.clearAllMocks();
});

const modes = [
  ["staging show-all", false, true, "dsx"],
  ["staging flag off", false, false, "classic"],
  ["launch without a staging export", false, undefined, "classic"],
  ["prototype with staging show-all", true, true, "dsx"],
  ["prototype with staging flag off", true, false, "dsx"],
  ["prototype without a staging export", true, undefined, "dsx"],
];

describe("staging show-all experiment surface", () => {
  it("selects DSX for staging or prototype without changing legacy callers", async () => {
    const { experimentSurfaceOf } = await load();
    for (const [label, prototype, showUnreleased, surface] of modes) {
      expect(experimentSurfaceOf({ prototype, showUnreleased }), label).toBe(
        surface,
      );
    }
    expect(experimentSurfaceOf({ prototype: false })).toBe("classic");
    expect(experimentSurfaceOf({ prototype: true })).toBe("dsx");
  });

  it.each(modes)(
    "%s selects %s / %s through the shared hook",
    async (_label, prototype, showUnreleased, surface) => {
      const { useExperimentSurface } = await load({ prototype, showUnreleased });
      const { result } = renderHook(useExperimentSurface);
      expect(result.current).toBe(surface);
    },
  );

  it.each(modes)(
    "%s keeps Compare access consistent with the selected surface",
    async (_label, prototype, showUnreleased, surface) => {
      const { useDsxOnlyCompareRedirect } = await load({
        prototype,
        showUnreleased,
      });
      const { result } = renderHook(useDsxOnlyCompareRedirect, {
        wrapper: CompareRoute,
      });
      if (surface === "dsx") {
        expect(result.current).toBeNull();
      } else {
        expect(result.current.type).toBe(Navigate);
        expect(result.current.props.to).toBe("/dashboard/develop/d1");
        expect(result.current.props.replace).toBe(true);
      }
    },
  );

  // The staging page for one experiment, its detail read answered as `marker`.
  async function openStagingExperiment(tab, marker) {
    await load({ prototype: false, showUnreleased: true });
    requests.get.mockReset();
    requests.get.mockImplementation(async (url) => {
      if (url === "/model-hub/experiments/v2/e1/") {
        return { data: { status: true, result: detailOf(marker) } };
      }
      throw new Error(`Unexpected GET on the staging experiment page: ${url}`);
    });
    // Loaded after load() so the provider and the page share one module.
    const { QueryClient, QueryClientProvider } = await import(
      "@tanstack/react-query"
    );
    const { default: ExperimentWrapper } = await import(
      "src/pages/dashboard/Develop/ExperimentWrapper"
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 5000 } },
    });
    render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          HelmetProvider,
          null,
          createElement(
            MemoryRouter,
            { initialEntries: [`/dashboard/develop/experiment/e1/${tab}`] },
            createElement(
              Routes,
              null,
              createElement(Route, {
                path: "/dashboard/develop/experiment/:experimentId/*",
                element: createElement(ExperimentWrapper),
              }),
            ),
          ),
        ),
      ),
    );
    return client;
  }

  it.each(["summary", "paths", "traces"])(
    "staging opens the DSX %s wrapper for an experiment it can show in full",
    async (tab) => {
      const client = await openStagingExperiment(tab, "with-runs");
      expect(await screen.findByTestId("dsx-experiment-page")).toBeVisible();
      expect(screen.queryByTestId("classic-experiment-page")).toBeNull();
      expect(requests.get).toHaveBeenCalledTimes(1);
      client.clear();
    },
  );

  it.each(["data", "summary", "paths"])(
    "staging opens the classic page at %s for an experiment created before runs were recorded",
    async (tab) => {
      const client = await openStagingExperiment(tab, "before-runs");
      expect(
        await screen.findByTestId("classic-experiment-page"),
      ).toBeVisible();
      expect(screen.queryByTestId("dsx-experiment-page")).toBeNull();
      expect(requests.get).toHaveBeenCalledTimes(1);
      client.clear();
    },
  );

  it("does not enable prototype mode or load/start the mock backend", async () => {
    const { prototypeModule, useExperimentSurface, useDsxOnlyCompareRedirect } =
      await load({ prototype: false, showUnreleased: true });
    expect(prototypeModule.isPrototypeMode()).toBe(false);
    const surface = renderHook(useExperimentSurface);
    const compare = renderHook(useDsxOnlyCompareRedirect, {
      wrapper: CompareRoute,
    });
    expect(surface.result.current).toBe("dsx");
    expect(compare.result.current).toBeNull();
    expect(prototypeModule.isPrototypeMode()).toBe(false);
    expect(prototypeModule.prototypeModeFromEnv(import.meta.env)).toBe(false);
    expect(import.meta.env.VITE_PROTOTYPE_MODE).toBe("false");
    expect(import.meta.env.VITE_ENABLE_MSW).toBe("false");
    expect(mockBackend.loaded).not.toHaveBeenCalled();
    expect(mockBackend.start).not.toHaveBeenCalled();
  });
});
