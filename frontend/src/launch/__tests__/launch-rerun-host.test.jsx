// Regression: with experimentsBasic OFF, the app shell's Rerun with
// changes host must not read
// GET experiments/v2/{id}/ on every experiment URL (index, data, summary,
// matrix, paths, traces) before the route guard redirects. This test runs
// the real Router, the real route guard (launchRedirect) and the real shell
// mount (layouts/dashboard RerunHostMount -> RerunWithChangesHost) against a
// recording axios adapter, and counts every request that names the
// experiment from the first render until the URL settles: a redirect alone,
// or nothing rendered, is not a pass.
//   - census section A / NOHIDE, OFF and ON: classic index/data/summary
//     mount the page and issue exactly its tagged GET; the rerun host reads
//     nothing. The index lands on data with the stubbed empty run list;
//   - NEW matrix/paths/traces routes still redirect with ZERO experiment
//     requests. Classic visibility never activates these Results children;
//   - a prototype build (no override): the host mounts and reads the
//     experiment (positive control for the shell mount).
import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Outlet, useLocation, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import axiosInstance from "src/utils/axios";
import { setLaunchOverrideForTest } from "../launchSwitch";
import Router from "src/routes/sections";
// Load the shell stand-in (below) now, before any case renders: its factory
// evaluates the real layout module, whose import time grows with the
// worker's load, so the router's lazy shell import must find it loaded
// rather than race the product's chunk-import timeout.
import "src/layouts/dashboard";

// Launch-build tests run with the show-unreleased switch off, in the override
// (launchSwitch reads it) and in the export the product modules read.
const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("../launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

// The launch's build env (MODE stays "test"): not a prototype build, so the
// route guard reads launchRedirect whatever env files the host has.
vi.hoisted(() => {
  vi.stubEnv("VITE_ENVIRONMENT", "dev");
  vi.stubEnv("VITE_PROTOTYPE_MODE", "false");
});

// Production caches the build mode at import time. Use its real env parser
// per case here so one suite can exercise both real and prototype builds.
vi.mock("src/utils/prototype", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    isPrototypeMode: () => actual.prototypeModeFromEnv(import.meta.env),
  };
});

const EXP = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f";
const DS = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const SUBPATHS = ["", "data", "summary", "matrix", "paths", "traces"];

const authState = { user: { organization_role: "Owner" } };
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => authState }));
/* eslint-disable react/prop-types */
vi.mock("src/auth/guard", async (importOriginal) => {
  const actual = await importOriginal();
  const pass = ({ children }) => children ?? <Outlet />;
  return Object.fromEntries(Object.keys(actual).map((name) => [name, pass]));
});
/* eslint-enable react/prop-types */
// The stand-in records its own load and its renders, so the suite can show it
// was loaded once, from the real layout module, outside every case, and that
// the router's lazy shell import resolves to it.
const shellStandIn = vi.hoisted(() => ({
  suiteStarted: false,
  loads: 0,
  loadedInCase: undefined,
  mount: null,
  renders: 0,
}));
// The layout's chrome (navs, Falcon, palette) is not under test; its shell
// mount of the Rerun with changes host is, so the stand-in renders the real
// RerunHostMount from the real layout module.
vi.mock("src/layouts/dashboard", async (importOriginal) => {
  const actual = await importOriginal();
  // A layout without RerunHostMount (before this fix) mounted the host on
  // every page as `<RerunWithChangesHost />`; the stand-in does the same.
  const { default: Host } = await import(
    "src/sections/common/RunSources/RerunWithChangesHost"
  );
  const { default: PropTypes } = await import("prop-types");
  const Mount = actual.RerunHostMount ?? (() => <Host />);
  const Shell = ({ children }) => {
    shellStandIn.renders += 1;
    return (
      <>
        {children ?? <Outlet />}
        <Mount />
      </>
    );
  };
  Shell.propTypes = { children: PropTypes.node };
  shellStandIn.loads += 1;
  // A reused worker can retain the previous file's currentTestName while
  // collecting this file. Only read it after this file's hooks have started.
  shellStandIn.loadedInCase = shellStandIn.suiteStarted
    ? (expect.getState().currentTestName ?? null)
    : null;
  shellStandIn.mount = Mount;
  return { ...actual, default: Shell };
});
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isSuccess: true,
    isLoading: false,
  }),
  usePostLoginPath: () => "/dashboard/develop",
}));
// The experiment page stand-in reads its experiment the way a real page
// does, tagged so the count tells the page's read from the host's.
vi.mock("src/pages/dashboard/Develop/ExperimentWrapper", async () => {
  const { useEffect } = await import("react");
  const { default: axios, endpoints } = await import("src/utils/axios");
  const Page = () => {
    const { experimentId } = useParams();
    useEffect(() => {
      axios
        .get(endpoints.develop.experiment.getExperimentDetails(experimentId), {
          params: { probe: "page" },
        })
        .catch(() => {});
    }, [experimentId]);
    return (
      <div data-testid="experiment-page">
        <Outlet />
      </div>
    );
  };
  return { default: Page };
});
const { stub } = vi.hoisted(() => ({
  stub: (id) => () => ({ default: () => <div data-testid={id} /> }),
}));
vi.mock("src/pages/dashboard/Develop/ExperimentData", stub("exp-data"));
vi.mock("src/pages/dashboard/Develop/ExperimentSummary", stub("exp-summary"));
vi.mock("src/pages/dashboard/Develop/ExperimentMatrix", stub("exp-matrix"));
vi.mock("src/pages/dashboard/Develop/ExperimentPaths", stub("exp-paths"));
vi.mock(
  "src/sections/experiment-detail/traces/ExperimentTraces",
  stub("exp-traces"),
);
vi.mock("src/pages/dashboard/Develop/Develop", stub("datasets-list"));
vi.mock("src/pages/dashboard/Develop/DevelopDetail", stub("dataset-page"));
vi.mock("src/sections/common/AddData/AddDataSheet", () => ({
  default: () => null,
}));
// The index route's own landing read (dashboard.jsx ExperimentIndexRedirect)
// answers at once, so every request that names the experiment is either the
// tagged page read or the host's.
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentDetail: () => ({ data: { runs: [] }, isError: false }),
}));

const requests = [];
let realAdapter;
beforeAll(() => {
  shellStandIn.suiteStarted = true;
  realAdapter = axiosInstance.defaults.adapter;
  axiosInstance.defaults.adapter = async (config) => {
    requests.push({
      method: String(config.method ?? "get").toUpperCase(),
      url: String(config.url ?? ""),
      probe: config.params?.probe ?? null,
    });
    return {
      data: { status: true, result: { dataset_id: DS, runs: [] } },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    };
  };
});
afterAll(() => {
  axiosInstance.defaults.adapter = realAdapter;
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.stubEnv("VITE_ENVIRONMENT", "dev");
  vi.stubEnv("VITE_PROTOTYPE_MODE", "false");
  requests.length = 0;
});

function CurrentUrl() {
  const loc = useLocation();
  return <p data-testid="url">{loc.pathname}</p>;
}

async function visit(url) {
  requests.length = 0;
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <MemoryRouter initialEntries={[url]}>
          <Router />
          <CurrentUrl />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  // Let the lazy shell and route chunks resolve (a page stand-in shows),
  // then let effects run and queries fire until the requests settle.
  const tick = () =>
    act(async () => {
      await new Promise((r) => setTimeout(r, 25));
    });
  const landed = () =>
    document.querySelector(
      '[data-testid="datasets-list"], [data-testid="experiment-page"]',
    );
  for (let i = 0; i < 400 && !landed(); i += 1) await tick(); // eslint-disable-line no-await-in-loop
  let seen = -1;
  for (let i = 0; i < 40 && seen !== requests.length; i += 1) {
    seen = requests.length;
    for (let j = 0; j < 4; j += 1) await tick(); // eslint-disable-line no-await-in-loop
  }
  const experimentReads = requests.filter((r) => r.url.includes(EXP));
  return {
    landed: Boolean(landed()),
    path: screen.getByTestId("url").textContent,
    experimentReads,
    hostReads: experimentReads.filter((r) => r.probe !== "page"),
    pageReads: experimentReads.filter((r) => r.probe === "page"),
    all: [...requests],
  };
}

const urlOf = (sub) =>
  `/dashboard/develop/experiment/${EXP}${sub ? `/${sub}` : ""}?datasetId=${DS}`;

describe("L6 shell stand-in loads before the first route renders", () => {
  it("is the real layout's RerunHostMount, loaded once outside every case, and the router's lazy shell import resolves to it", async () => {
    // Loaded at file import, the stand-in (and the real layout module it
    // wraps) is ready before any case renders, so no route render waits on
    // it inside the product's chunk-import timeout.
    expect(shellStandIn.loads).toBe(1);
    expect(shellStandIn.loadedInCase).toBeNull();
    const layout = await import("src/layouts/dashboard");
    expect(typeof layout.RerunHostMount).toBe("function");
    expect(shellStandIn.mount).toBe(layout.RerunHostMount);
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: false,
      madeReal: [],
    });
    const rendersBefore = shellStandIn.renders;
    const r = await visit(urlOf("data"));
    expect(r.landed).toBe(true);
    expect(r.path).toBe(`/dashboard/develop/experiment/${EXP}/data`);
    expect(screen.getByTestId("experiment-page")).toBeTruthy();
    expect(r.experimentReads).toEqual([
      {
        method: "GET",
        url: `/model-hub/experiments/v2/${EXP}/`,
        probe: "page",
      },
    ]);
    expect(r.hostReads).toEqual([]);
    expect(shellStandIn.renders).toBeGreaterThan(rendersBefore);
    expect(shellStandIn.loads).toBe(1);
  });
});

describe("L6: no experiment read before a hidden experiment URL redirects", () => {
  SUBPATHS.forEach((sub) => {
    it(`default launch build (OFF): ${sub || "index"} permits only classic page reads or redirects with zero experiment requests`, async () => {
      setLaunchOverrideForTest({
        launch: true,
        showUnreleased: false,
        experimentsBasic: false,
        madeReal: [],
      });
      const r = await visit(urlOf(sub));
      if (["", "data", "summary"].includes(sub)) {
        // exp.classic owns these routes, independently of exp.page/Results.
        // The index's useExperimentDetail is stubbed above: exactly one GET
        // comes from the mounted ExperimentWrapper stand-in, never the host.
        expect(r.path).toBe(
          `/dashboard/develop/experiment/${EXP}/${sub || "data"}`,
        );
        expect(screen.getByTestId("experiment-page")).toBeTruthy();
        expect(screen.queryByTestId("datasets-list")).toBeNull();
        expect(r.experimentReads).toEqual([
          {
            method: "GET",
            url: `/model-hub/experiments/v2/${EXP}/`,
            probe: "page",
          },
        ]);
        expect(r.hostReads).toEqual([]);
      } else {
        expect(r.path).toBe("/dashboard/develop");
        expect(screen.queryByTestId("experiment-page")).toBeNull();
        expect(screen.getByTestId("datasets-list")).toBeTruthy();
        expect(
          r.experimentReads.map((x) => `${x.method} ${x.url}`),
          `${sub || "index"}: requests naming the experiment`,
        ).toEqual([]);
      }
    });
  });

  it("experimentsBasic ON: the supported data page reads its experiment (positive control); the host reads nothing", async () => {
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: true,
      madeReal: [],
    });
    const r = await visit(urlOf("data"));
    expect(r.path).toBe(`/dashboard/develop/experiment/${EXP}/data`);
    expect(screen.getByTestId("experiment-page")).toBeTruthy();
    expect(r.pageReads.length).toBeGreaterThanOrEqual(1);
    expect(r.hostReads).toEqual([]);
  });

  ["", "summary", "matrix", "paths", "traces"].forEach((sub) => {
    it(`experimentsBasic ON: ${sub || "index"} reads nothing through the host`, async () => {
      setLaunchOverrideForTest({
        launch: true,
        showUnreleased: false,
        experimentsBasic: true,
        madeReal: [],
      });
      const r = await visit(urlOf(sub));
      expect(r.hostReads).toEqual([]);
      if (["", "summary"].includes(sub)) {
        // The classic Summary and index routes stay available even though
        // NEW Results children remain hidden with experimentsBasic ON.
        expect(r.path).toBe(
          `/dashboard/develop/experiment/${EXP}/${sub || "data"}`,
        );
        expect(screen.getByTestId("experiment-page")).toBeTruthy();
        expect(r.experimentReads).toEqual([
          {
            method: "GET",
            url: `/model-hub/experiments/v2/${EXP}/`,
            probe: "page",
          },
        ]);
      } else {
        // The DSX result tabs stay hidden with the flag ON: redirect, and
        // no request names the experiment at all.
        expect(r.path).toBe("/dashboard/develop");
        expect(r.experimentReads).toEqual([]);
      }
    });
  });

  it("prototype build: the shell mounts the host and it reads the experiment (positive control)", async () => {
    vi.stubEnv("VITE_ENVIRONMENT", "local");
    vi.stubEnv("VITE_PROTOTYPE_MODE", "true");
    const r = await visit(urlOf("matrix"));
    expect(r.path).toBe(`/dashboard/develop/experiment/${EXP}/matrix`);
    expect(
      r.hostReads.some((x) =>
        x.url.includes(`/model-hub/experiments/v2/${EXP}/`),
      ),
    ).toBe(true);
  });

  it("real build with rerun claimed: the data page reads but the prototype host reads nothing", async () => {
    setLaunchOverrideForTest({
      launch: true,
      showUnreleased: false,
      experimentsBasic: true,
      madeReal: ["exp.rerun"],
    });
    const r = await visit(urlOf("data"));
    expect(r.path).toBe(`/dashboard/develop/experiment/${EXP}/data`);
    expect(screen.getByTestId("experiment-page")).toBeTruthy();
    expect(r.pageReads.length).toBeGreaterThanOrEqual(1);
    expect(r.hostReads).toEqual([]);
  });
});

// The release as shipped: the same launch overrides with the show-unreleased
// switch left on, in the override and in the export the product modules read.
// Each Results route renders its page instead of redirecting. The shell mounts
// the Rerun with changes host (its surface shows), but the host reads only in
// a prototype build, so the page's own read is the only experiment request.
// The Results pages are stand-ins here, so no page text renders.
describe("experiment Results routes as shipped (unreleased parts shown)", () => {
  afterEach(() => {
    unreleased.shown = false;
  });

  const shipAs = (experimentsBasic) => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic, madeReal: [] });
    unreleased.shown = true;
  };

  const rendersPage = async (sub) => {
    const r = await visit(urlOf(sub));
    expect(r.path).toBe(`/dashboard/develop/experiment/${EXP}/${sub}`);
    expect(screen.getByTestId("experiment-page")).toBeTruthy();
    expect(screen.getByTestId(`exp-${sub}`)).toBeTruthy();
    expect(screen.queryByTestId("datasets-list")).toBeNull();
    expect(r.experimentReads).toEqual([
      {
        method: "GET",
        url: `/model-hub/experiments/v2/${EXP}/`,
        probe: "page",
      },
    ]);
    expect(r.hostReads).toEqual([]);
  };

  ["matrix", "paths", "traces"].forEach((sub) => {
    it(`experimentsBasic OFF: ${sub} renders its page, whose read is the only experiment request`, async () => {
      shipAs(false);
      await rendersPage(sub);
    });

    it(`experimentsBasic ON: ${sub} renders its page and reads nothing through the host`, async () => {
      shipAs(true);
      await rendersPage(sub);
    });
  });
});
