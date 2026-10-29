import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import {
  act,
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

// Shared-host render latency is not a product timeout. Keep asynchronous DOM
// waits explicit; the transport is still immediate and each test is bounded.
configure({ asyncUtilTimeout: 10000 });
import axios, { endpoints } from "src/utils/axios";
import DerivedMetricsView from "../DerivedMetricsView";
import { initialForm } from "../metricForm";
import monitorResponses from "./fixtures/monitorResponses.json";

const context = vi.hoisted(() => ({
  organization: "11111111-1111-4111-8111-111111111111",
  workspace: "22222222-2222-4222-8222-222222222222",
  readOnly: false,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: context.organization }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: context.workspace,
    isReady: true,
  }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: context.readOnly }),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => ({
    rows: [
      { id: "33333333-3333-4333-8333-333333333333", name: "Synthetic project" },
    ],
    totalPages: 1,
  }),
}));

const metricId = "44444444-4444-4444-8444-444444444444";
const runId = "55555555-5555-4555-8555-555555555555";
const definition = {
  ...initialForm(),
  // Historical custom recipe; new metric defaults must not change this fixture.
  formula: "ratio(resolved == 1 and escalated == 0, resolved >= 0)",
  inputs: ["resolved", "escalated"].map((alias) => ({
    alias,
    source: "span",
    field: "attribute_number",
    attribute_key: alias,
    reducer: "latest",
  })),
  version: 1,
  definition_hash: "a".repeat(64),
  created_at: "2026-10-20T00:00:00Z",
};
const metric = {
  id: metricId,
  project_id: "33333333-3333-4333-8333-333333333333",
  name: "Resolution",
  description: "Saved definition",
  current_version: 2,
  definition: {
    ...definition,
    version: 2,
    formula: "sum(resolved)",
    selection_unit: "user",
    weighting: "inverse_probability",
  },
};
const savedRun = {
  id: runId,
  metric_id: metricId,
  metric_version: 1,
  definition,
  window_start: "2026-10-25T23:00:00Z",
  window_end: "2026-10-26T00:00:00Z",
  as_of: "2026-10-26T00:01:00Z",
  status: "complete",
  error: "",
  result_id: "66666666-6666-4666-8666-666666666666",
  snapshot_id: "77777777-7777-4777-8777-777777777777",
  filters: [],
  row_count: 2,
};
const result = {
  group_key: "",
  value: 0.5,
  numerator: 1,
  denominator: 2,
  status: "ok",
  unit_count: 2,
  valid_count: 2,
  missing_count: 0,
  invalid_probability_count: 0,
  uncertainty: {
    confidence_level: 0.95,
    status: "not_estimable",
    reason: "unstable_variance",
    method: "iid_units",
    interval: "",
    ci_low: null,
    ci_high: null,
    std_error: null,
    effective_n: 2,
    degrees_of_freedom: 1,
  },
};
let adapter;
let oldAdapter;
let currentRun;
let currentResult;
let monitorResponse;
let failValidation;
let failMonitor;
let failRuns;
let failSave;
let failPersistRun;
let savedDefinition;
let latestDefinition;
const dashboardId = "99999999-9999-4999-8999-999999999999";
let client;

beforeEach(() => {
  localStorage.clear(); // Each case starts without another case's durable draft.
  window.history.replaceState({}, "", "/dashboard/evaluations/metrics");
  context.organization = "11111111-1111-4111-8111-111111111111";
  context.workspace = "22222222-2222-4222-8222-222222222222";
  context.readOnly = false;
  currentRun = { ...savedRun };
  currentResult = { ...result };
  monitorResponse = monitorResponses.success;
  failValidation = false;
  failMonitor = false;
  failRuns = false;
  failSave = null;
  failPersistRun = false;
  savedDefinition = null;
  latestDefinition = metric;
  oldAdapter = axios.defaults.adapter;
  // Do not mock axios.post or either contract interceptor. Only the transport
  // boundary is synthetic; invalid writes must fail before reaching this adapter.
  adapter = vi.fn(async (config) => {
    let data;
    if (config.url === endpoints.derivedMetrics.list) {
      if (config.method === "post") {
        const body = JSON.parse(config.data);
        savedDefinition = {
          ...metric,
          ...body,
          id: "88888888-8888-4888-8888-888888888888",
          current_version: 1,
          definition: { ...body, version: 1 },
        };
        data = savedDefinition;
      } else
        data = {
          results: [metric, ...(savedDefinition ? [savedDefinition] : [])],
          next_cursor: null,
        };
    } else if (config.url === endpoints.derivedMetrics.detail(metricId)) {
      if (config.method === "put") {
        if (failSave === 409)
          throw {
            config,
            response: {
              status: 409,
              data: { error: "Version changed" },
              config,
            },
          };
        if (failSave) throw new Error("Synthetic lost save response");
        const body = JSON.parse(config.data);
        latestDefinition = {
          ...latestDefinition,
          current_version: latestDefinition.current_version + 1,
          definition: {
            ...body,
            version: latestDefinition.current_version + 1,
          },
        };
      }
      data = latestDefinition;
    } else if (
      savedDefinition &&
      config.url === endpoints.derivedMetrics.runs(savedDefinition.id)
    )
      data = { results: [], next_cursor: null };
    else if (config.url === endpoints.derivedMetrics.runs(metricId)) {
      if (config.method === "post") {
        if (failPersistRun) throw new Error("Synthetic lost run response");
        data = savedRun;
      } else {
        if (failRuns) throw new Error("synthetic run-list outage");
        data = { results: [savedRun], next_cursor: null };
      }
    } else if (config.url === endpoints.derivedMetrics.run(runId))
      data = currentRun;
    else if (config.url === endpoints.derivedMetrics.results(runId))
      data = {
        result_id: currentRun.result_id,
        results: [currentResult],
        next_cursor: null,
      };
    else if (config.url === endpoints.derivedMetrics.recompute(runId)) {
      currentRun = { ...currentRun, status: "queued" };
      data = currentRun;
    } else if (config.url === endpoints.derivedMetrics.validate) {
      if (failValidation) throw new Error("Synthetic draft validation failed");
      data = {
        valid: true,
        kind: "ratio",
        required_inputs: [],
        semantics: "Valid formula",
      };
    } else if (config.url === endpoints.dashboard.create) {
      data = {
        status: true,
        result: { id: dashboardId, name: "Pinned metric" },
      };
    } else if (config.url === endpoints.dashboard.widgets(dashboardId)) {
      throw new Error("Synthetic lost widget response");
    } else if (config.url === endpoints.project.createMonitor) {
      if (failMonitor)
        throw { config, response: { ...monitorResponses.server, config } };
      if (!monitorResponse)
        throw new Error("synthetic private transport failure");
      const response = { ...monitorResponse, config, headers: {} };
      if (response.status >= 400) throw { config, response };
      return response;
    } else
      throw new Error(
        `Unexpected controlled request: ${config.method} ${config.url}`,
      );
    return { data, status: 200, statusText: "OK", headers: {}, config };
  });
  axios.defaults.adapter = adapter;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  axios.defaults.adapter = oldAdapter;
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <DerivedMetricsView />
      </SnackbarProvider>
    </QueryClientProvider>,
  );
async function selectMetric() {
  fireEvent.click(await screen.findByRole("button", { name: /^Resolution$/ }));
}
async function selectRun() {
  fireEvent.click(await screen.findByRole("tab", { name: "Past runs" }));
  const history = await screen.findByRole("table", {
    name: "Past metric runs",
  });
  fireEvent.click(within(history).getByRole("button"));
  fireEvent.click(screen.getByRole("tab", { name: "Result" }));
  await screen.findByRole("region", { name: "Selected saved run" });
  fireEvent.click(screen.getByRole("tab", { name: "Contributors" }));
  await screen.findByRole("table", { name: "Metric results" });
  fireEvent.click(screen.getByRole("tab", { name: "Result" }));
}
async function refreshSelectedRun() {
  const action = screen.queryByRole("button", {
    name: /^(Refresh run status|Retry saved result)$/,
  });
  if (action) fireEvent.click(action);
  // Complete/failed settled runs refresh through the existing background read.
  else
    await act(async () => client.refetchQueries({ queryKey: ["metric-run"] }));
}
async function openMonitor() {
  fireEvent.click(
    await screen.findByRole("button", { name: "Create monitor" }),
  );
  await screen.findByLabelText("Monitor name");
}
async function openEditor(review = true) {
  fireEvent.click(
    await screen.findByRole("button", { name: /^(Edit|View) definition$/ }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  if (review) fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

async function submitMonitor() {
  mount();
  await selectMetric();
  await openMonitor();
  fireEvent.change(screen.getByLabelText("Monitor name"), {
    target: { value: "Resolution monitor" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create monitor" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Monitor feedback")).toBeInTheDocument(),
  );
  const feedback = screen.getByLabelText("Monitor feedback");
  if (/Monitor created\.|Could not confirm/.test(feedback.textContent)) {
    const create = screen.getByRole("button", { name: "Create monitor" });
    expect(create).toBeDisabled();
    fireEvent.click(create);
  }
  expect(
    adapter.mock.calls.filter(
      ([c]) => c.url === endpoints.project.createMonitor,
    ),
  ).toHaveLength(1);
  return feedback;
}

describe("source-produced monitor responses through real Axios", () => {
  it.each(["false", "true"])(
    "accepts actual HTTP 200 success without a contract warning, strict=%s",
    async (strict) => {
      vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", strict);
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      expect(await submitMonitor()).toHaveTextContent("Monitor created.");
      expect(warn).not.toHaveBeenCalled();
    },
  );
  for (const strict of ["false", "true"]) {
    it.each([
      ["entitlement", /organization’s monitor limit/],
      ["entitlement_unavailable", /organization admin.*plan.*monitor limit/],
      ["forbidden", /Workspace member access/],
      ["validation", /Check notification email, then retry/],
      ["validation_drf", /Check critical threshold, then retry/],
      ["server", /may already exist.*do not retry blindly/],
    ])(
      "handles normalized %s locally, strict=" + strict,
      async (key, expected) => {
        vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", strict);
        monitorResponse = monitorResponses[key];
        const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
        const feedback = await submitMonitor();
        expect(feedback).toHaveTextContent(expected);
        expect(feedback).not.toHaveTextContent(
          /synthetic private|contract validation|upgrade_cta|ENTITLEMENT_DENIED/,
        );
        expect(warn).not.toHaveBeenCalled();
      },
    );
  }
  it.each(
    ["false", "true"].flatMap((strict) =>
      [
        {},
        { status: true, result: { id: "wrong-shape" } },
        { status: false, result: "not created" },
      ].map((data) => [strict, data]),
    ),
  )(
    "rejects malformed success without blind retry or auto resend, strict=%s, data=%j",
    async (strict, data) => {
      vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", strict);
      monitorResponse = { status: 200, data };
      expect(await submitMonitor()).toHaveTextContent(
        /Could not confirm.*may already exist/,
      );
      expect(screen.queryByText(/Monitor created\./)).not.toBeInTheDocument();
    },
  );
  it("does not treat missing response as pre-create denial", async () => {
    monitorResponse = null;
    expect(await submitMonitor()).toHaveTextContent(
      /may already exist.*do not retry blindly/,
    );
  });
  it("keeps an unknown normalized 400 failure ambiguous", async () => {
    monitorResponse = {
      status: 400,
      data: {
        ...monitorResponses.server.data,
        type: "client_error",
        code: "unknown_failure",
      },
    };
    expect(await submitMonitor()).toHaveTextContent(
      /Could not confirm.*may already exist/,
    );
  });
  it("sanitizes unknown validation field names and exception details", async () => {
    monitorResponse = {
      status: 422,
      data: {
        ...monitorResponses.validation.data,
        attr: "constructor",
        details: { constructor: ["synthetic private exception"] },
      },
    };
    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
    const feedback = await submitMonitor();
    expect(feedback).toHaveTextContent(
      /Check the name, threshold and notification email, then retry/,
    );
    expect(feedback).not.toHaveTextContent(
      /constructor|synthetic private|function|Object/,
    );
  });
  it("recognizes a known entitlement code before generic 403 permission copy", async () => {
    monitorResponse = {
      status: 403,
      data: { ...monitorResponses.forbidden.data, code: "ENTITLEMENT_LIMIT" },
    };
    expect(await submitMonitor()).toHaveTextContent(
      /organization’s monitor limit/,
    );
  });
});

describe("separate definition, run and result actions", () => {
  it("validates and saves a new guided definition without executing a run", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "New metric" }));
    fireEvent.change(screen.getByLabelText("Metric name"), {
      target: { value: "Average latency" },
    });
    fireEvent.mouseDown(screen.getByLabelText("Project"));
    fireEvent.click(
      await screen.findByRole("option", { name: "Synthetic project" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
    await screen.findByText(/Saved version 1\. Saving does not run/);
    expect(
      adapter.mock.calls.filter(
        ([c]) => c.url === endpoints.derivedMetrics.validate,
      ),
    ).toHaveLength(1);
    const writes = adapter.mock.calls.filter(
      ([c]) => c.url === endpoints.derivedMetrics.list && c.method === "post",
    );
    expect(writes).toHaveLength(1);
    expect(JSON.parse(writes[0][0].data)).toMatchObject({
      formula: "mean(value)",
      project_id: metric.project_id,
      inputs: [
        {
          alias: "value",
          source: "span",
          field: "latency_ms",
          reducer: "latest",
        },
      ],
    });
    expect(
      adapter.mock.calls.filter(
        ([c]) =>
          c.method !== "get" &&
          c.url !== endpoints.derivedMetrics.validate &&
          c.url !== endpoints.derivedMetrics.list,
      ),
    ).toHaveLength(0);
  });
  it.each([409, "unknown"])(
    "does not duplicate a definition write on %s and preserves the draft/expected version",
    async (failure) => {
      failSave = failure;
      mount();
      await selectMetric();
      await openEditor();
      fireEvent.click(
        screen.getByRole("button", { name: "Advanced configuration" }),
      );
      fireEvent.mouseDown(screen.getByLabelText("Weighting"));
      fireEvent.click(screen.getByRole("option", { name: /Unweighted/ }));
      fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
      await screen.findByText(
        failure === 409
          ? /This definition changed/
          : /Could not confirm the save/,
      );
      expect(
        screen.getByRole("button", { name: "Save new version" }),
      ).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
      const writes = adapter.mock.calls.filter(([c]) => c.method === "put");
      expect(writes).toHaveLength(1);
      expect(JSON.parse(writes[0][0].data)).toMatchObject({
        expected_version: 2,
        formula: "sum(resolved)",
        weighting: "none",
      });
      expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
    },
  );
  it("reloads a conflicted definition losslessly and saves against the refreshed optimistic version", async () => {
    failSave = 409;
    mount();
    await selectMetric();
    await openEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Advanced configuration" }),
    );
    fireEvent.mouseDown(screen.getByLabelText("Weighting"));
    fireEvent.click(screen.getByRole("option", { name: /Unweighted/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await screen.findByText(/This definition changed/);
    latestDefinition = {
      ...metric,
      current_version: 3,
      definition: {
        ...metric.definition,
        version: 3,
        weighting: "none",
        formula: "mean(resolved)",
        filters: [{ field: "model", value: "new-model" }],
      },
    };
    failSave = null;
    fireEvent.click(
      screen.getByRole("button", { name: "Reload saved definition" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await screen.findByText(/Saved version 3/);
    fireEvent.click(
      screen.getByRole("button", { name: "Advanced configuration" }),
    );
    expect(
      screen.getByText(/Definition filters: model = new-model/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    // Two existing inputs mean Custom, even though only one is used. No inputs are dropped.
    expect(screen.getByLabelText("Calculation")).toHaveTextContent(
      "Custom formula",
    );
    expect(screen.getByLabelText("Formula")).toHaveValue("mean(resolved)");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await screen.findByText(/Saved version 4\. Saving does not run/);
    const writes = adapter.mock.calls
      .filter(([c]) => c.method === "put")
      .map(([c]) => JSON.parse(c.data));
    expect(writes.map((body) => body.expected_version)).toEqual([2, 3]);
    expect(writes[1]).toMatchObject({
      formula: "mean(resolved)",
      filters: [{ field: "model", value: "new-model" }],
      inputs: latestDefinition.definition.inputs,
    });
  });

  it("retries an uncertain run with the same idempotency key and exact window", async () => {
    failPersistRun = true;
    mount();
    await selectMetric();
    fireEvent.click(screen.getByRole("button", { name: "Run metric" }));
    fireEvent.change(screen.getByLabelText("Window start"), {
      target: { value: "2026-10-20T00:00:01.123" },
    });
    fireEvent.change(screen.getByLabelText("Window end"), {
      target: { value: "2026-10-21T00:00:02.456" },
    });
    for (let i = 0; i < 2; i += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Run and persist" }));
      await screen.findByText("Synthetic lost run response");
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Run and persist" }),
        ).toBeEnabled(),
      );
    }
    const bodies = adapter.mock.calls
      .filter(
        ([c]) =>
          c.url === endpoints.derivedMetrics.runs(metricId) &&
          c.method === "post",
      )
      .map(([c]) => JSON.parse(c.data));
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0]).toMatchObject({
      version: 2,
      window_start: new Date("2026-10-20T00:00:01.123").toISOString(),
      window_end: new Date("2026-10-21T00:00:02.456").toISOString(),
    });
  });
  it("pins the exact selected run and blocks duplicate dashboard creation after a lost widget response", async () => {
    mount();
    await selectMetric();
    await selectRun();
    fireEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    fireEvent.change(screen.getByLabelText("Dashboard name"), {
      target: { value: "Pinned metric" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    await screen.findByText(
      /dashboard was created, but the widget could not be confirmed/,
    );
    const create = screen.getByRole("button", { name: "Create dashboard" });
    expect(create).toBeDisabled();
    fireEvent.click(create);
    expect(
      adapter.mock.calls.filter(([c]) => c.url === endpoints.dashboard.create),
    ).toHaveLength(1);
    const widget = adapter.mock.calls.find(
      ([c]) => c.url === endpoints.dashboard.widgets(dashboardId),
    )[0];
    expect(JSON.parse(widget.data).query_config).toMatchObject({
      time_range: {
        custom_start: savedRun.window_start,
        custom_end: savedRun.window_end,
      },
      metrics: [{ type: "derived_metric", derived_run_id: runId }],
    });
  });
});

describe("metrics repair through the real Axios contract interceptor", () => {
  it("Create monitor reaches the adapter with the scoped organization and project", async () => {
    mount();
    await selectMetric();
    await openMonitor();
    fireEvent.change(screen.getByLabelText("Monitor name"), {
      target: { value: "Resolution monitor" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create monitor" }));
    await waitFor(() =>
      expect(
        adapter.mock.calls.filter(
          ([c]) => c.url === endpoints.project.createMonitor,
        ),
      ).toHaveLength(1),
    );
    const request = adapter.mock.calls.find(
      ([c]) => c.url === endpoints.project.createMonitor,
    )[0];
    expect(JSON.parse(request.data)).toMatchObject({
      organization: context.organization,
      project: metric.project_id,
      metric: metricId,
      metric_type: "derived_metric",
    });
    expect(await screen.findByText(/Monitor created/)).not.toHaveTextContent(
      /record alerts and use/,
    );
  });

  it("the same interceptor rejects a monitor without organization before the adapter", async () => {
    await expect(
      axios.post(endpoints.project.createMonitor, {
        name: "Invalid",
        project: metric.project_id,
        metric_type: "derived_metric",
        metric: metricId,
        threshold_operator: "less_than",
      }),
    ).rejects.toMatchObject({
      message: expect.stringContaining("organization"),
    });
    expect(adapter).not.toHaveBeenCalled();
  });

  it("a workspace change remounts the editor and discards stale monitor actions", async () => {
    const view = mount();
    await selectMetric();
    await openMonitor();
    fireEvent.change(screen.getByLabelText("Monitor name"), {
      target: { value: "Old draft" },
    });
    context.organization = "99999999-9999-4999-8999-999999999999";
    context.workspace = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    view.rerender(
      <QueryClientProvider client={client}>
        <SnackbarProvider>
          <DerivedMetricsView />
        </SnackbarProvider>
      </QueryClientProvider>,
    );
    expect(
      screen.queryByRole("button", { name: "Create monitor" }),
    ).not.toBeInTheDocument();
    await selectMetric();
    await openMonitor();
    expect(screen.getByLabelText("Monitor name")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Monitor name"), {
      target: { value: "New scope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create monitor" }));
    await waitFor(() =>
      expect(
        adapter.mock.calls.some(
          ([c]) => c.url === endpoints.project.createMonitor,
        ),
      ).toBe(true),
    );
    expect(
      JSON.parse(
        adapter.mock.calls.find(
          ([c]) => c.url === endpoints.project.createMonitor,
        )[0].data,
      ).organization,
    ).toBe(context.organization);
  });

  it("keeps monitor failure actionable and local without internal contract copy", async () => {
    failMonitor = true;
    mount();
    await selectMetric();
    await openMonitor();
    fireEvent.change(screen.getByLabelText("Monitor name"), {
      target: { value: "Monitor" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create monitor" }));
    const message = await screen.findByText(
      /Could not confirm monitor creation/,
    );
    expect(message).toHaveTextContent(/do not retry blindly/i);
    expect(
      screen.queryByText(/synthetic private exception/),
    ).not.toBeInTheDocument();
    expect(message.closest('[aria-label="Monitor feedback"]')).not.toBeNull();
  });

  it.each(["saved definition", "New metric"])(
    "clears draft errors when loading %s",
    async (target) => {
      failValidation = true;
      mount();
      await selectMetric();
      await openEditor();
      fireEvent.click(screen.getByRole("button", { name: "Validate formula" }));
      await screen.findByText("Synthetic draft validation failed");
      fireEvent.click(
        screen.getByRole("button", {
          name: "Cancel editing",
        }),
      );
      if (target === "New metric") {
        fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
        fireEvent.click(
          await screen.findByRole("button", { name: "New metric" }),
        );
      }
      expect(
        screen.queryByText("Synthetic draft validation failed"),
      ).not.toBeInTheDocument();
    },
  );

  it("retains a run-list read failure when discarding a draft error", async () => {
    failRuns = true;
    mount();
    await selectMetric();
    fireEvent.click(await screen.findByRole("tab", { name: "Past runs" }));
    await screen.findByText(/Past runs could not be loaded/);
    fireEvent.click(screen.getByRole("tab", { name: "Result" }));
    fireEvent.click(screen.getByRole("tab", { name: "Past runs" }));
    expect(
      screen.getByText(/Past runs could not be loaded/),
    ).toBeInTheDocument();
  });

  it("uses selected v1 provenance above the v2 editor, not the current definition", async () => {
    mount();
    await selectMetric();
    await selectRun();
    const summary = screen.getByRole("region", { name: "Selected saved run" });
    expect(summary).toHaveTextContent("0.5");
    expect(summary).toHaveTextContent(/session/);
    expect(summary).toHaveTextContent(/Unweighted/);
    expect(summary).toHaveTextContent(definition.formula);
    expect(summary).not.toHaveTextContent("sum(resolved)");
    expect(summary).toHaveTextContent(/Point estimate only/);
    expect(summary).toHaveTextContent(/UTC|GMT/);
    expect(screen.queryByLabelText("Metric name")).not.toBeInTheDocument();
    const savedContext = summary.textContent;
    await openEditor(false);
    expect(screen.getByLabelText("Formula")).toHaveValue("sum(resolved)");
    expect(
      screen.queryByRole("region", { name: "Selected saved run" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel editing" }));
    expect(
      screen.getByRole("region", { name: "Selected saved run" }).textContent,
    ).toBe(savedContext);
  });

  it("keeps new-run boundaries readable and separate from the selected saved window", async () => {
    mount();
    await selectMetric();
    await selectRun();
    const summary = screen.getByRole("region", { name: "Selected saved run" });
    const savedContext = summary.textContent;
    fireEvent.click(screen.getByRole("button", { name: "Run metric" }));
    for (const label of ["Window start", "Window end"]) {
      const field = screen.getByLabelText(label);
      expect(field).toHaveAttribute("type", "datetime-local");
      expect(field).toHaveAttribute("step", "any");
      expect(field.closest(".MuiFormControl-root")).toHaveClass(
        "MuiFormControl-fullWidth",
      );
    }
    expect(
      screen.getByText(/Enter boundaries in local time/),
    ).toHaveTextContent(/saved windows are shown in UTC/);
    fireEvent.change(screen.getByLabelText("Window start"), {
      target: { value: "2026-10-20T03:30" },
    });
    expect(summary.textContent).toBe(savedContext);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await openEditor(false);
    fireEvent.change(screen.getByLabelText("Formula"), {
      target: { value: "sum(escalated)" },
    });
    expect(
      screen.queryByRole("button", { name: "Run metric" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel editing" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("region", { name: "Selected saved run" }).textContent,
    ).toBe(savedContext);
  });

  it("Viewer values remain focusable and read-only while mutation actions stay disabled", async () => {
    context.readOnly = true;
    mount();
    await selectMetric();
    for (const name of ["Run metric", "Create monitor"]) {
      const actions = screen.getAllByRole("button", { name });
      actions.forEach((action) => {
        expect(action).toBeDisabled();
        fireEvent.click(action);
      });
    }
    fireEvent.click(screen.getByRole("button", { name: "View definition" }));
    expect(screen.getByLabelText("Summarize one value per")).toHaveValue(
      "user",
    );
    expect(screen.getByLabelText("Input 1 alias")).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const formula = screen.getByLabelText("Formula");
    expect(formula).not.toBeDisabled();
    expect(formula).toHaveAttribute("readonly");
    formula.focus();
    expect(formula).toHaveFocus();
    fireEvent.keyDown(formula, { key: "x" });
    fireEvent.change(formula, { target: { value: "sum(escalated)" } });
    expect(formula).toHaveValue("sum(resolved)");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    for (const name of ["Save new version", "Validate formula"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name }));
    }
    expect(adapter.mock.calls.filter(([c]) => c.method !== "get")).toHaveLength(
      0,
    );
  });

  it("updates the top summary only when a new committed result identity arrives", async () => {
    mount();
    await selectMetric();
    await selectRun();
    for (const status of ["queued", "running", "failed"]) {
      currentRun = { ...currentRun, status };
      await refreshSelectedRun();
      await waitFor(() =>
        expect(
          screen.getByRole("region", { name: "Selected saved run" }),
        ).toHaveTextContent(`Recompute ${status}`),
      );
      const region = screen.getByRole("region", { name: "Selected saved run" });
      expect(region).toHaveTextContent(
        /last committed result, not a new result/,
      );
      expect(
        within(region).getByRole("heading", { name: "0.50" }),
      ).toBeInTheDocument();
    }
    expect(
      adapter.mock.calls.filter(
        ([c]) =>
          c.url === endpoints.derivedMetrics.results(runId) &&
          c.params?.limit !== 1,
      ),
    ).toHaveLength(1);
    currentRun = {
      ...currentRun,
      status: "complete",
      result_id: "88888888-8888-4888-8888-888888888888",
    };
    currentResult = { ...result, value: 0, numerator: 0 };
    await refreshSelectedRun();
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("region", { name: "Selected saved run" }),
        ).getByRole("heading", { name: "0.00" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("region", { name: "Selected saved run" }),
    ).not.toHaveTextContent(/Recompute|last committed/);
    expect(
      adapter.mock.calls.filter(
        ([c]) =>
          c.url === endpoints.derivedMetrics.results(runId) &&
          c.params?.limit !== 1,
      ),
    ).toHaveLength(2);
  });

  it("recompute uses the current run status in Past runs while retaining the committed value", async () => {
    mount();
    await selectMetric();
    await selectRun();
    fireEvent.click(screen.getByRole("tab", { name: "Contributors" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Recompute frozen run" }),
    );
    await screen.findByText(/Run queued/);
    fireEvent.click(screen.getByRole("tab", { name: "Past runs" }));
    expect(
      screen.getByRole("table", { name: "Past metric runs" }),
    ).toHaveTextContent(/queued/);
    expect(
      screen.getByRole("table", { name: "Past metric runs" }),
    ).not.toHaveTextContent(/complete/);
    fireEvent.click(screen.getByRole("tab", { name: "Contributors" }));
    expect(
      screen.getByText(/Showing the last committed result/),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("table", { name: "Metric results" })).getByText(
        "0.5",
      ),
    ).toBeInTheDocument();
  });
});
