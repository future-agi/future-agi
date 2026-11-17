/* eslint-disable react/prop-types */
// Real header, results composition, run column header and React Query observers.
// Only transport and peripheral UI are replaced. R1/R3 deliberately KEEP their
// stored Running status after a cooperative pause, as results_catalog does.
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExperimentRow from "../ExperimentRow";
import RunResultsView from "../results/RunResultsView";
import {
  useExperimentDetail,
  datasetRunsKey,
  datasetRunsParams,
} from "src/api/develop/runs";
import { R1_CONFIG_INCLUDE } from "src/api/develop/r2";
import { resetRunControlForTest } from "src/sections/common/ExperimentRunControl/runControlAvailability";
import { useLiveCounts } from "../results/liveCountShare";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const h = vi.hoisted(() => ({
  detail: null,
  detailRead: null,
  catalog: null,
  grid: null,
  stop: null,
  sent: [],
  toasts: [],
  noop: () => {},
}));
const envelope = (result) => ({ data: { status: true, result } });
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    ...(await importOriginal()).default,
    get: async (url) => {
      h.sent.push({ method: "get", url });
      if (url.endsWith("/results/"))
        return { data: { status: true, result: h.grid } };
      if (url.endsWith("/runs/"))
        return { data: { status: true, result: h.catalog } };
      if (url.includes("/experiments/v2/"))
        return h.detailRead ?? { data: { status: true, result: h.detail } };
      throw new Error(`Unexpected read: ${url}`);
    },
    post: (url, body) => {
      h.sent.push({ method: "post", url, body });
      return h.stop;
    },
  },
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: () => ({ data: null }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("src/api/develop/r4", async (importOriginal) => ({
  ...(await importOriginal()),
  useVoiceSummary: () => ({ data: null }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { run: true, edit: true, view: true },
    reason: () => null,
    ask: [],
  }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (message, options = {}) => {
    h.toasts.push({ message, options });
  },
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ title, children }) => (
    <span data-tip={typeof title === "string" ? title : undefined}>
      {children}
    </span>
  ),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Audit/AuditDrawer", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("../DevelopExperimentSelect", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span>Experiment seven</span>,
}));
vi.mock("../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/iterate",
}));
vi.mock("src/sections/common/ux/useShortcuts", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => {},
}));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: () => {},
}));
vi.mock("src/sections/common/ux/oneview/ViewMenu", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/oneview/Section", () => ({
  useSections: () => ({}),
}));
vi.mock("src/sections/common/ux/oneview/useViewportDensity", () => ({
  default: () => "regular",
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, h.noop],
}));
vi.mock("src/sections/common/ValueViewer/useValueViewMode", () => ({
  default: () => ["formatted", h.noop],
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ request: h.noop, dialog: null }),
}));
vi.mock("src/sections/develop/useDatasetColumnsLite", () => ({
  default: () => ({ data: [] }),
}));
vi.mock("../results/useResultsPrefs", () => ({
  default: () => ({ showReasons: false, setShowReasons: h.noop }),
}));
vi.mock("../results/useResultsColumns", () => ({
  default: () => ({ columns: [], visibleIds: [] }),
}));
vi.mock("../results/headers/useEvalVersionDoor", () => ({
  useEvalVersionDoor: () => ({ dialog: null }),
}));
vi.mock("../paths/usePagePair", () => ({ default: () => ({}) }));
vi.mock("../results/ResultsToolbar", () => ({
  default: () => null,
  anyWorseTip: () => "",
  showLabel: (v) => v,
}));
vi.mock("../results/FilterPills", () => ({ default: () => null }));
vi.mock("../results/SelectionBar", () => ({ default: () => null }));
vi.mock("../results/EvalDetailsDialog", () => ({ default: () => null }));
vi.mock("../results/SeeWhyButton", () => ({ default: () => null }));
vi.mock("../results/ResultsVerdict", () => ({ default: () => null }));
vi.mock("src/sections/common/filters/ResultsExpressionDialog", () => ({
  default: () => null,
}));
vi.mock("../ExperimentBarRightSection/ExperimentBarDataRightSection", () => ({
  default: () => null,
}));
vi.mock("../results/ResultDrawer", () => ({ default: ({ grid }) => grid }));
vi.mock("../results/ResultsGrid", async () => {
  const { ResultRunColumnHeader } = await import(
    "../ExperimentData/CustomExperimentColumnHeader"
  );
  return {
    default: React.forwardRef(function Grid({ context }, _ref) {
      return (
        <div data-testid="inner-run-card">
          {Object.values(context.runsById).map((run) => (
            <ResultRunColumnHeader key={run.run_id} run={run} />
          ))}
        </div>
      );
    }),
  };
});

const run = (status = "running", done = 8) => ({
  run_id: RUN,
  experiment_id: EXP,
  dataset_id: "ds",
  label: "#7",
  name: "Prompt",
  status,
  progress: {
    done,
    total: 40,
    scored: 6,
    errors: 0,
    status: status === "cancelled" ? "Cancelled" : "Running",
  },
});
const detail = (status = "running", done = 8) => ({
  id: EXP,
  ordinal: 7,
  dataset_id: "ds",
  name: "Experiment seven",
  status,
  runs: [run(status, done)],
  runs_total: 1,
  runs_has_more: false,
});
let client;
let finishStop;
let failStop;
function Page({ mode = "experiment" }) {
  const { data } = useExperimentDetail(EXP);
  return (
    <>
      <div data-testid="header">
        <ExperimentRow />
      </div>
      {data && (
        <RunResultsView
          datasetId="ds"
          experimentId={EXP}
          runIds={[RUN]}
          baselineId={RUN}
          mode={mode}
          initialRuns={data.runs}
          onBaselineChange={h.noop}
        />
      )}
    </>
  );
}
function openPage(mode) {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[`/dashboard/develop/experiment/${EXP}/data`]}
      >
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/data"
            element={<Page mode={mode} />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const tips = (element) =>
  [...element.querySelectorAll("[data-tip]")].map((node) =>
    node.getAttribute("data-tip"),
  );
const stops = () => h.toasts.filter((t) => t.options.key === "exp-stop");
const lastStop = () => stops().at(-1)?.message;
async function clickStop() {
  fireEvent.click(await screen.findByTestId("exp-stop"));
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Stop run",
    }),
  );
  await waitFor(() =>
    expect(h.sent.some((r) => r.method === "post")).toBe(true),
  );
}
async function acknowledge(status = "STOPPED") {
  await act(async () =>
    finishStop(
      envelope({
        experiment_id: EXP,
        runs: [{ run_id: RUN, name: "Prompt", status }],
      }),
    ),
  );
}
async function expectKept(n) {
  await waitFor(() =>
    expect(lastStop()).toBe(`Stopped #7 · ${n} of 40 rows kept`),
  );
  expect(tips(screen.getByTestId("header"))).toContain(
    `Stopped · ${n} of 40 rows kept`,
  );
  expect(tips(screen.getByTestId("inner-run-card"))).toContain(
    `Stopped at ${n} of 40 rows`,
  );
  expect(
    within(screen.getByTestId("inner-run-card")).getByText("Stopped", {
      exact: true,
    }),
  ).toBeInTheDocument();
  expect(
    tips(screen.getByTestId("inner-run-card")).some((tip) =>
      tip.startsWith("Running ·"),
    ),
  ).toBe(false);
}
beforeEach(() => {
  resetRunControlForTest();
  useLiveCounts.setState({ byExperiment: {}, stateByExperiment: {} });
  h.sent = [];
  h.toasts = [];
  h.detail = detail();
  h.detailRead = null;
  h.catalog = {
    count: 1,
    offset: 0,
    has_more: false,
    results: [run("running", 3)],
  };
  h.grid = {
    total: 0,
    rows: [],
    scope: { complete: true },
    pagination: { page: 0, has_more: false },
    summary: { examples_total: 40, runs: [run("running", 4)], metrics: [] },
  };
  h.stop = new Promise((resolve, reject) => {
    finishStop = resolve;
    failStop = reject;
  });
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("cooperative Stop state and kept rows", () => {
  it("reconciles the real header, inner card and toast to detail's 10 of 40, not stale R1/R3 or scored", async () => {
    const otherKey = ["experiment", "other-experiment"];
    const other = envelope({ runs: [{ ...run(), run_id: "other-run" }] });
    client.setQueryData(otherKey, other);
    openPage();
    await waitFor(() =>
      expect(tips(screen.getByTestId("inner-run-card"))).toContain(
        "Running · 4 of 40 rows",
      ),
    );
    await clickStop();
    expect(stops()).toEqual([]); // No successful Stop before the request resolves.
    let finishRead;
    h.detailRead = new Promise((resolve) => {
      finishRead = resolve;
    });
    await acknowledge(); // Actual response contract: no done or total fields.
    expect(lastStop()).not.toMatch(/\d+ of \d+ rows kept/);
    h.detail = detail("cancelled", 10);
    await act(async () => finishRead(envelope(h.detail)));
    await expectKept(10);
    expect(client.getQueryData(otherKey)).toBe(other);
    const cfgKey = [
      ...datasetRunsKey("ds"),
      datasetRunsParams({ include: R1_CONFIG_INCLUDE }),
    ];
    expect(client.getQueryData(cfgKey).data.result.results[0].status).toBe(
      "running",
    );
    expect(
      h.sent.filter((r) => r.method === "post").map((r) => r.body),
    ).toEqual([{ run_ids: [RUN] }]);
  });

  it("keeps STOPPING non-final until a later detail read confirms the drained stop", async () => {
    openPage();
    await clickStop();
    await acknowledge("STOPPING");
    await waitFor(() => expect(stops().length).toBeGreaterThan(0));
    expect(lastStop()).not.toMatch(/Stopped|\d+ of \d+ rows kept/);
    expect(tips(screen.getByTestId("inner-run-card"))).toContain(
      "Running · 4 of 40 rows",
    );
    expect(
      within(screen.getByTestId("inner-run-card")).queryByText("Stopped", {
        exact: true,
      }),
    ).toBeNull();
    h.detail = detail("cancelled", 10);
    await act(async () =>
      client.invalidateQueries({ queryKey: ["experiment", EXP] }),
    );
    await expectKept(10);
  });

  it("does not mark a failed Stop as stopped or show success", async () => {
    openPage();
    await clickStop();
    await act(async () =>
      failStop({ message: "Stop refused", statusCode: 503 }),
    );
    await waitFor(() => expect(stops().at(-1)?.options.variant).toBe("error"));
    expect(lastStop()).not.toMatch(/^Stopped/);
    expect(
      client.getQueryData(["experiment", EXP]).data.result.runs[0].status,
    ).toBe("running");
    expect(tips(screen.getByTestId("inner-run-card"))).toContain(
      "Running · 4 of 40 rows",
    );
    expect(
      within(screen.getByTestId("inner-run-card")).queryByText("Stopped", {
        exact: true,
      }),
    ).toBeNull();
  });

  it("preserves a genuine zero from the stopped catalog instead of the pre-stop count", async () => {
    openPage();
    await clickStop();
    h.detail = detail("cancelled", 0);
    await acknowledge();
    await expectKept(0);
  });

  it("does not invent zero kept rows when the stopped catalog count is absent", async () => {
    openPage();
    await clickStop();
    h.detail = detail("cancelled", null);
    await acknowledge();
    await waitFor(() => expect(lastStop()).toMatch(/^Stopped #7/));
    expect(lastStop()).toMatch(/count.*unavailable/i);
    expect(lastStop()).not.toMatch(/\d+ of \d+/);
  });

  it("does not overlay experiment control state onto a compare scope", async () => {
    h.detail = detail("cancelled", 10);
    openPage("compare");
    await waitFor(() =>
      expect(tips(screen.getByTestId("inner-run-card"))).toContain(
        "Running · 4 of 40 rows",
      ),
    );
    expect(tips(screen.getByTestId("inner-run-card"))).not.toContain(
      "Stopped at 10 of 40 rows",
    );
    expect(stops()).toEqual([]);
  });
});
