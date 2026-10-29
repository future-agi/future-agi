import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
  onlineManager,
} from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import ProductionThemeProvider from "src/theme";
import axios, { endpoints } from "src/utils/axios";
import WidgetChart from "src/sections/dashboards/WidgetChart";
import DerivedMetricsView from "../DerivedMetricsView";
import wire from "./fixtures/committedResults.json";
import dashboard from "src/sections/dashboards/__tests__/fixtures/savedMetricResponse.json";
import boundaryCases from "src/sections/dashboards/__tests__/fixtures/preciseBoundaryCases.json";
import "src/global.css";

configure({ asyncUtilTimeout: 10000 });
const scope = vi.hoisted(() => ({
  org: "11111111-1111-4111-8111-111111111111",
  ws: "22222222-2222-4222-8222-222222222222",
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({
    themeMode: "light",
    themeColorPresets: "default",
    themeDirection: "ltr",
    themeContrast: "default",
  }),
}));
vi.mock("src/hooks/use-system-theme-mode", () => ({
  useSystemThemeMode: () => "light",
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: scope.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: scope.ws, isReady: true }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: false }),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => ({
    rows: [
      { id: "00000000-0000-0000-0000-000000000002", name: "Synthetic project" },
    ],
    totalPages: 1,
  }),
}));
const clone = (value) => JSON.parse(JSON.stringify(value));
const metricId = wire.run.metric_id,
  runId = wire.run.id,
  r1 = wire.old_page.result_id,
  r2 = wire.new_page.result_id;
const metric = {
  id: metricId,
  project_id: dashboard.config.project_ids[0],
  name: "Race metric",
  description: "Not history",
  current_version: 2,
  definition: {
    ...wire.run.definition,
    version: 2,
    formula: "sum(resolved)",
    weighting: "inverse_probability",
    selection_unit: "user",
  },
};
const otherMetric = {
  ...metric,
  id: "00000000-0000-0000-0000-000000000009",
  name: "Other metric",
};
let client,
  oldAdapter,
  adapter,
  currentRun,
  currentPage,
  runHandler,
  pageHandler,
  contributorHandler,
  contributorPage,
  dashboardResponse;
const key = (id = r1, rollup = "overall", after = "") => [
  "metric-results",
  `${scope.org}:${scope.ws}`,
  runId,
  id,
  rollup,
  after,
];
const calls = (url) =>
  adapter.mock.calls.filter(([config]) => config.url === url);
const summary = () =>
  screen.getByRole("region", { name: "Selected saved run" });
const heading = (value) =>
  within(summary()).getByRole("heading", { name: Number(value).toFixed(2) });
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  window.history.replaceState({}, "", "/dashboard/evaluations/metrics");
  scope.org = "11111111-1111-4111-8111-111111111111";
  scope.ws = "22222222-2222-4222-8222-222222222222";
  currentRun = clone(wire.run);
  currentPage = clone(wire.old_page);
  runHandler = null;
  pageHandler = null;
  contributorHandler = null;
  contributorPage = clone(wire.contributors);
  dashboardResponse = clone(dashboard.response);
  client = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5000,
        gcTime: 60000,
        refetchOnWindowFocus: true,
        refetchOnMount: true,
        refetchOnReconnect: true,
        retry: 1,
      },
      mutations: { retry: false },
    },
  });
  oldAdapter = axios.defaults.adapter;
  adapter = vi.fn(async (config) => {
    let data;
    if (config.url === endpoints.derivedMetrics.list)
      data = { results: [metric, otherMetric], next_cursor: null };
    else if (config.url === endpoints.derivedMetrics.runs(metricId))
      data = { results: [clone(wire.run)], next_cursor: null };
    else if (config.url === endpoints.derivedMetrics.runs(otherMetric.id))
      data = { results: [], next_cursor: null };
    else if (config.url === endpoints.derivedMetrics.run(runId))
      data = runHandler ? await runHandler(config) : clone(currentRun);
    else if (config.url === endpoints.derivedMetrics.results(runId))
      data = pageHandler ? await pageHandler(config) : clone(currentPage);
    else if (config.url === endpoints.derivedMetrics.contributors(runId))
      data = contributorHandler
        ? await contributorHandler(config)
        : clone(contributorPage);
    else if (config.url === endpoints.dashboard.query)
      data = clone(dashboardResponse);
    else throw new Error(`Unexpected offline route ${config.url}`);
    return { data, status: 200, statusText: "OK", headers: {}, config };
  });
  axios.defaults.adapter = adapter;
});
afterEach(() => {
  cleanup();
  client.clear();
  axios.defaults.adapter = oldAdapter;
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
function wrap(children) {
  return (
    <BrowserRouter>
      <ProductionThemeProvider>
        <QueryClientProvider client={client}>
          <SnackbarProvider>{children}</SnackbarProvider>
        </QueryClientProvider>
      </ProductionThemeProvider>
    </BrowserRouter>
  );
}
async function selectRun({ expectValue = true } = {}) {
  window.history.replaceState(
    { idx: 0 },
    "",
    `/dashboard/evaluations/metrics/${metricId}?run=${runId}`,
  );
  const view = render(wrap(<DerivedMetricsView />));
  if (expectValue)
    await waitFor(() => expect(heading(0.5)).toBeInTheDocument());
  // The existing Contributors tab is the single evidence action.
  fireEvent.click(await screen.findByRole("tab", { name: "Contributors" }));
  return view;
}
async function refreshSelectedRun() {
  const action = screen.queryByRole("button", {
    name: /^(Refresh run status|Retry saved result)$/,
  });
  if (action) fireEvent.click(action);
  // Settled terminal runs have no status-refresh control. Model the existing
  // background run reread instead; all transport/identity/count assertions stay.
  else
    await act(async () => client.refetchQueries({ queryKey: ["metric-run"] }));
}
async function refreshBy(event = "focus") {
  const now = Date.now() + 6000;
  vi.spyOn(Date, "now").mockReturnValue(now);
  await act(async () => {
    if (event === "focus") {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    } else {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    }
  });
}
async function changeSelect(label, name) {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(await screen.findByRole("option", { name }));
}

describe("independent Axios/run/result reads with actual production providers", () => {
  it.each(["false", "true"])(
    "focus straddles a commit, retains the old pair and bounds reconciliation; strict=%s",
    async (strict) => {
      vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", strict);
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      // The direct result action starts the independent contributor read now.
      // Hold that response across the commit race, then return R2 for retained R1.
      const contributorGate = deferred();
      contributorHandler = () => contributorGate.promise;
      await selectRun();
      currentRun = { ...currentRun, status: "running" };
      await refreshSelectedRun();
      await waitFor(() =>
        expect(summary()).toHaveTextContent("Recompute running"),
      );
      currentPage = clone(wire.new_page); // ONLY results advances; run still returns R1.
      const runReads = calls(endpoints.derivedMetrics.run(runId)).length;
      await refreshBy();
      await waitFor(() =>
        expect(client.getQueryState(key()).status).toBe("error"),
      );
      expect(client.getQueryData(key())).toEqual(wire.old_page);
      expect(heading(0.5)).toBeInTheDocument();
      expect(
        within(screen.getByRole("table", { name: "Metric results" })).getByText(
          "0.5",
        ),
      ).toBeInTheDocument();
      expect(summary()).toHaveTextContent(
        /Run running.*previously verified result/,
      );
      expect(summary()).not.toHaveTextContent(
        /not a new result|sum\(resolved\)/,
      );
      await waitFor(() =>
        expect(calls(endpoints.derivedMetrics.run(runId))).toHaveLength(
          runReads + 2,
        ),
      ); // focus + ONE reconciliation
      expect(calls(endpoints.derivedMetrics.results(runId))).toHaveLength(2);
      expect(
        screen.getByRole("button", { name: "Create dashboard" }),
      ).toBeDisabled();
      expect(calls(endpoints.dashboard.create)).toHaveLength(0);
      // A second independent endpoint must not attach R2 contributors to retained R1.
      contributorPage = { ...contributorPage, result_id: r2 };
      await act(async () => contributorGate.resolve(clone(contributorPage)));
      contributorHandler = null;
      await screen.findByText(/Contributors could not be verified/);
      expect(
        screen.queryByRole("table", { name: "Metric contributors" }),
      ).not.toBeInTheDocument();
      expect(calls(endpoints.derivedMetrics.contributors(runId))).toHaveLength(
        1,
      );
      expect(warn).not.toHaveBeenCalled();
      // Run is now R2, but the R2 page remains independently in flight.
      const gate = deferred();
      pageHandler = () => gate.promise;
      currentRun = { ...currentRun, result_id: r2, status: "complete" };
      await refreshSelectedRun();
      await waitFor(() =>
        expect(client.getQueryState(key(r2)).fetchStatus).toBe("fetching"),
      );
      expect(heading(0.5)).toBeInTheDocument();
      expect(summary()).toHaveTextContent(
        /Run complete.*previously verified result/,
      );
      expect(client.getQueryData(key(r2))).toBeUndefined();
      await act(async () => gate.resolve(clone(wire.new_page)));
      await waitFor(() => expect(heading(0)).toBeInTheDocument());
      expect(client.getQueryData(key(r1))).toEqual(wire.old_page);
      expect(client.getQueryData(key(r2))).toEqual(wire.new_page);
      expect(summary()).not.toHaveTextContent(/Recompute|reconciled/);
      expect(
        screen.getByRole("button", { name: "Create dashboard" }),
      ).toBeEnabled();
    },
  );

  it("stops after a second commit race and allows explicit recovery", async () => {
    await selectRun();
    const r3 = "00000000-0000-0000-0000-000000000010";
    let reads = 0;
    runHandler = () => {
      reads += 1;
      return { ...wire.run, result_id: reads === 1 ? r1 : r2 };
    };
    pageHandler = () => ({ ...wire.new_page, result_id: reads < 2 ? r2 : r3 });
    await refreshBy("reconnect");
    await waitFor(() =>
      expect(client.getQueryState(key(r2))?.status).toBe("error"),
    );
    expect(reads).toBe(2); // reconnect + single reconciliation, not another loop for R3
    expect(calls(endpoints.derivedMetrics.results(runId))).toHaveLength(3);
    expect(client.getQueryData(key(r2))).toBeUndefined();
    expect(heading(0.5)).toBeInTheDocument();
    runHandler = () => ({ ...wire.run, result_id: r3 });
    await refreshSelectedRun();
    await waitFor(() => expect(heading(0)).toBeInTheDocument());
    expect(client.getQueryData(key(r3)).result_id).toBe(r3);
  });

  it.each([undefined, null, "untrusted"])(
    "never infers missing/untrusted result identity %s",
    async (identity) => {
      currentPage = { ...wire.old_page, result_id: identity };
      vi.spyOn(console, "warn").mockImplementation(() => {});
      await selectRun({ expectValue: false });
      await waitFor(() =>
        expect(client.getQueryState(key())?.status).toBe("error"),
      );
      expect(client.getQueryData(key())).toBeUndefined();
      expect(summary()).toHaveTextContent(/identity is not verified.*not zero/);
      expect(
        screen.queryByRole("table", { name: "Metric results" }),
      ).not.toBeInTheDocument();
      expect(calls(endpoints.derivedMetrics.results(runId))).toHaveLength(1);
      currentPage = clone(wire.old_page);
      await refreshSelectedRun();
      await waitFor(() => expect(heading(0.5)).toBeInTheDocument());
    },
  );

  it("retains a matched snapshot on a strict legacy response without converting it to zero", async () => {
    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
    await selectRun();
    currentPage = { results: [], next_cursor: null }; // old backend, no trustworthy identity
    await refreshBy();
    await waitFor(() =>
      expect(client.getQueryState(key()).status).toBe("error"),
    );
    expect(client.getQueryData(key())).toEqual(wire.old_page);
    expect(heading(0.5)).toBeInTheDocument();
    expect(summary()).toHaveTextContent(/previously verified/);
  });

  it.each(["ws", "org", "metric", "run"])(
    "cancels in-flight reads on %s changes and ignores late bodies",
    async (change) => {
      const view = await selectRun();
      const gate = deferred();
      pageHandler = () => gate.promise;
      await refreshBy();
      await waitFor(() =>
        expect(calls(endpoints.derivedMetrics.results(runId))).toHaveLength(2),
      );
      const oldRequest = calls(endpoints.derivedMetrics.results(runId)).at(
        -1,
      )[0];
      if (change === "metric") {
        fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
        fireEvent.click(
          await screen.findByRole("button", { name: "Other metric" }),
        );
      } else if (change === "run") {
        fireEvent.click(screen.getByRole("tab", { name: "Past runs" }));
        fireEvent.click(
          screen.getByRole("button", { name: "Clear selected run" }),
        );
      } else {
        scope[change] = "99999999-9999-4999-8999-999999999999";
        view.rerender(wrap(<DerivedMetricsView />));
      }
      await waitFor(() => expect(oldRequest.signal.aborted).toBe(true));
      await act(async () => gate.resolve(clone(wire.new_page)));
      expect(
        screen.queryByRole("region", { name: "Selected saved run" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("table", { name: "Metric results" }),
      ).not.toBeInTheDocument();
    },
  );

  it("keeps rollup/cursor provenance separate and recovers a stale cursor without mixing pages", async () => {
    await selectRun();
    pageHandler = (config) =>
      clone(config.params.after ? wire.session_next : wire.session_page);
    await changeSelect("Group results by", "session");
    await waitFor(() =>
      expect(
        screen.getByRole("table", { name: "Metric results" }),
      ).toHaveTextContent("session-a"),
    );
    expect(
      within(summary()).queryByRole("heading", { name: "0.50" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next results" }));
    await waitFor(() =>
      expect(
        screen.getByRole("table", { name: "Metric results" }),
      ).toHaveTextContent("session-b"),
    );
    expect(
      screen.getByRole("table", { name: "Metric results" }),
    ).not.toHaveTextContent("session-a");
    let runReads = 0;
    runHandler = () => ({ ...wire.run, result_id: ++runReads === 1 ? r1 : r2 });
    pageHandler = (config) => {
      if (config.params.after)
        throw {
          config,
          response: {
            status: 400,
            data: {
              error:
                "Result cursor is invalid or belongs to another result/rollup. Start from the first page.",
            },
            config,
          },
        };
      return {
        ...clone(wire.new_page),
        results: [{ ...wire.new_page.results[0], group_key: "session-new" }],
      };
    };
    await refreshBy();
    await waitFor(() =>
      expect(
        screen.getByRole("table", { name: "Metric results" }),
      ).toHaveTextContent("session-new"),
    );
    expect(
      screen.getByRole("table", { name: "Metric results" }),
    ).not.toHaveTextContent("session-b");
    expect(
      calls(endpoints.derivedMetrics.results(runId)).at(-1)[0].params.after,
    ).toBe("");
    expect(
      client.getQueryData(key(r1, "session", wire.session_page.next_cursor)),
    ).toEqual(wire.session_next);
  });

  it("retains the old verified pair across all current statuses until an independent replacement arrives", async () => {
    await selectRun();
    const gate = deferred();
    pageHandler = () => gate.promise;
    for (const status of ["queued", "running", "failed", "complete"]) {
      currentRun = { ...wire.run, result_id: r2, status };
      await refreshSelectedRun();
      await waitFor(() =>
        expect(summary()).toHaveTextContent(
          `Run ${status}. Keeping the previously verified result`,
        ),
      );
      expect(heading(0.5)).toBeInTheDocument();
      expect(summary()).toHaveTextContent("version 1");
      expect(summary()).not.toHaveTextContent("sum(resolved)");
    }
    const immutableContext = summary().textContent;
    fireEvent.click(screen.getByRole("button", { name: "Run metric" }));
    fireEvent.change(screen.getByLabelText("Window end"), {
      target: { value: "2026-10-20T03:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(summary().textContent).toBe(immutableContext);
    fireEvent.click(screen.getByRole("button", { name: "Edit definition" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.mouseDown(screen.getByLabelText("Calculation"));
    fireEvent.click(screen.getByRole("option", { name: "Custom formula" }));
    fireEvent.change(screen.getByLabelText("Formula"), {
      target: { value: "sum(resolved) + 100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel editing" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(heading(0.5)).toBeInTheDocument();
    expect(summary()).not.toHaveTextContent("100");
    expect(summary().textContent).toBe(immutableContext);
    await act(async () => gate.resolve(clone(wire.new_page)));
    await waitFor(() => expect(heading(0)).toBeInTheDocument());
    expect(summary()).not.toHaveTextContent("reconciled");
  });

  it("cancels a late R1 response after R2 has been independently verified", async () => {
    await selectRun();
    const late = deferred();
    pageHandler = () => late.promise;
    await refreshBy();
    await waitFor(() =>
      expect(calls(endpoints.derivedMetrics.results(runId))).toHaveLength(2),
    );
    const first = calls(endpoints.derivedMetrics.results(runId)).at(-1)[0];
    currentRun = { ...wire.run, result_id: r2 };
    pageHandler = () => clone(wire.new_page);
    await refreshSelectedRun();
    await waitFor(() => expect(heading(0)).toBeInTheDocument());
    expect(first.signal.aborted).toBe(true);
    await act(async () => late.resolve(clone(wire.old_page)));
    expect(heading(0)).toBeInTheDocument();
    expect(client.getQueryData(key(r2))).toEqual(wire.new_page);
    expect(client.getQueryData(key(r1))).toEqual(wire.old_page);
  });

  it("does not use a run response from another metric to label rows", async () => {
    currentRun.metric_id = otherMetric.id;
    await selectRun({ expectValue: false });
    await screen.findByText(/Selected run identity could not be verified/);
    expect(
      screen.queryByRole("region", { name: "Selected saved run" }),
    ).not.toBeInTheDocument();
    expect(calls(endpoints.derivedMetrics.results(runId))).toHaveLength(0);
  });

  it("renders microsecond exclusion recovery through the production dashboard read", async () => {
    const testCase = boundaryCases.find(
      (c) => c.boundary === "end" && c.delta_us === -1,
    );
    const result = dashboardResponse.result.metrics[0];
    result.derived_context.run = clone(testCase.saved_run);
    delete result.derived_context.result;
    result.series[0].data = [];
    const restore = vi.fn();
    render(
      wrap(
        <WidgetChart
          widget={{
            id: "precise-point",
            query_config: dashboard.config,
            chart_config: { chart_type: "metric" },
          }}
          globalDateRange={testCase.range}
          onRestoreSavedWindow={restore}
        />,
      ),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore saved window" }),
    );
    expect(restore).toHaveBeenCalledTimes(1);
    expect(calls(endpoints.dashboard.query)).toHaveLength(1);
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });
});
