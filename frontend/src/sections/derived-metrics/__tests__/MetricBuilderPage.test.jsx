import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({
  metric: null,
  capabilities: null,
  catalog: null,
  evaluators: null,
  organizationId: "11111111-1111-4111-8111-111111111111",
  workspaceId: "11111111-1111-4111-8111-111111111111",
  role: "Owner",
  create: vi.fn(),
  update: vi.fn(),
  validate: vi.fn(),
  get: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: state.role }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: state.organizationId }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: state.workspaceId }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get },
  endpoints: {
    project: { listProjects: () => "/tracer/project/list_project_ids/" },
  },
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({ data: [] }),
}));
vi.mock("../EvaluatorBinding", () => ({ default: () => null }));
vi.mock("../usePreview", () => ({ default: () => ({ state: "idle" }) }));
vi.mock("../api", () => ({
  useMetricDetail: () => ({
    data: state.metric,
    readState: state.detailReadState,
    refetch: () => {},
  }),
  useMetricCapabilities: () => ({
    data: state.capabilities,
    readState: "ready",
  }),
  useMetricCatalog: (args) => {
    state.catalogArgs = args;
    return {
      data: state.catalogFor ? state.catalogFor(args.params) : state.catalog,
      readState: "ready",
    };
  },
  useMetricEvaluators: () => ({ data: state.evaluators, readState: "ready" }),
  useCreateMetric: () => ({ mutateAsync: state.create }),
  useUpdateMetric: () => ({ mutateAsync: state.update }),
  useValidateMetric: () => ({ mutateAsync: state.validate }),
}));
import MetricBuilderPage from "../MetricBuilderPage";
import { capabilities, catalog, evaluators, id } from "./fixtures/builder";
const renderPage = (path = `/dashboard/metrics/new?project=${id}`) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/dashboard/metrics/new"
            element={<MetricBuilderPage />}
          />
          <Route
            path="/dashboard/metrics/:metricId/edit"
            element={<MetricBuilderPage />}
          />
          <Route
            path="/dashboard/metrics"
            element={<p>Metric list destination</p>}
          />
          <Route
            path="/dashboard/metrics/:metricId"
            element={<p>Metric detail destination</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
const choose = (label, name) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(screen.getByRole("option", { name }));
};
beforeEach(() => {
  localStorage.clear();
  state.catalogFor = null;
  state.metric = null;
  state.detailReadState = "ready";
  state.role = "Owner";
  state.capabilities = capabilities;
  state.catalog = catalog;
  state.evaluators = evaluators;
  state.create.mockReset().mockResolvedValue({ id });
  state.update.mockReset().mockResolvedValue({ id });
  state.validate.mockReset().mockResolvedValue({ valid: true });
  state.get.mockResolvedValue({
    data: { result: { projects: [{ id, name: "Support" }] } },
  });
});
afterEach(cleanup);
describe("metric builder page", () => {
  it("keeps the title bar and its way back while the metric loads or fails", async () => {
    state.detailReadState = "loading";
    renderPage("/dashboard/metrics/metric-a/edit");
    expect(screen.getByRole("heading", { name: "Edit metric" })).toBeTruthy();
    expect(
      screen.getByRole("progressbar", { name: "Loading metric" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Back to metrics" }),
    ).toBeTruthy();
    cleanup();
    state.detailReadState = "unavailable";
    renderPage("/dashboard/metrics/metric-a/edit");
    expect(screen.getByText("Metric definition unavailable.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back to metrics" }));
    await screen.findByText("Metric detail destination");
  });
  it("saves a typed sentence through validation and the F02 create hook", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Project" }).value).toBe(
        "Support",
      ),
    );
    fireEvent.change(screen.getByLabelText("Metric name"), {
      target: { value: "Resolved share" },
    });
    choose("Choose a source", "Resolved");
    fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
    await screen.findByText("Metric detail destination");
    expect(state.validate).toHaveBeenCalledOnce();
    expect(state.create).toHaveBeenCalledOnce();
    expect(state.create.mock.calls[0][0]).toMatchObject({
      kind: "share",
      level: "conversation",
      project_id: id,
      name: "Resolved share",
      inputs: [
        expect.objectContaining({
          value_type: "boolean",
          attribute_key: "resolved",
        }),
      ],
    });
  });
  it("keeps an existing custom formula byte-for-byte and saves its expected version", async () => {
    const definition = {
      kind: "custom",
      level: "conversation",
      formula: "ratio(sum(score), count(score))",
      inputs: [
        {
          alias: "score",
          source: "eval",
          config_id: id,
          config_hash: "a".repeat(64),
          reducer: "latest",
        },
      ],
      selection_unit: "session",
      weighting: "inverse_probability",
      filters: [{ field: "model", value: "a" }],
      cohort_key: "locale",
      field_spec: {},
      sampling: { mode: "saved" },
    };
    state.metric = {
      id,
      project_id: id,
      name: "Existing",
      description: "Do not convert",
      current_version: 4,
      definition: null,
      draft_definition: definition,
    };
    renderPage(`/dashboard/metrics/${id}/edit`);
    await screen.findByLabelText("Formula");
    fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
    await screen.findByText("Metric detail destination");
    expect(state.update).toHaveBeenCalledWith({
      ...definition,
      project_id: id,
      name: "Existing",
      description: "Do not convert",
      expected_version: 4,
    });
  });
  it("shows legacy user recipes read-only and requires an explicit new user version", async () => {
    state.metric = {
      id,
      project_id: id,
      name: "Legacy",
      description: "",
      current_version: 2,
      draft_definition: {
        kind: "custom",
        level: "conversation",
        selection_unit: "user",
        formula: "mean(value)",
        inputs: [
          {
            alias: "value",
            source: "span",
            field: "latency_ms",
            reducer: "latest",
          },
        ],
        needs_rebuild: true,
      },
    };
    renderPage(`/dashboard/metrics/${id}/edit`);
    await screen.findByText("This legacy user metric needs a rebuild");
    expect(screen.getByRole("button", { name: "Save metric" }).disabled).toBe(
      true,
    );
    expect(screen.getByLabelText("Formula").disabled).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "Rebuild as user metric" }),
    );
    expect(screen.getByText(/"formula": "mean\(value\)"/)).toBeTruthy();
    choose("Choose a source", "Resolved");
    fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
    await screen.findByText("Metric detail destination");
    expect(state.update.mock.calls[0][0]).toMatchObject({
      kind: "share",
      level: "user",
      selection_unit: "user",
      user_rule: "any",
      expected_version: 2,
    });
  });
  it("guards Cancel and beforeunload, and only discards after confirmation", async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("Metric name"), {
      target: { value: "Unsaved" },
    });
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByLabelText("Metric name").value).toBe("Unsaved");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await screen.findByText("Metric list destination");
    expect(state.create).not.toHaveBeenCalled();
  });
  it("keeps validation failures in place and never creates a metric", async () => {
    state.validate.mockRejectedValue(new Error("Invalid source"));
    renderPage();
    fireEvent.change(screen.getByLabelText("Metric name"), {
      target: { value: "Draft" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Choose a source" }),
      ).toBeTruthy(),
    );
    choose("Choose a source", "Resolved");
    fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
    await screen.findByRole("alert");
    expect(state.create).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Metric name").value).toBe("Draft");
  });
  it("does not enable writes for viewers", () => {
    state.role = "Viewer";
    renderPage();
    expect(screen.getByRole("button", { name: "Save metric" }).disabled).toBe(
      true,
    );
    expect(screen.getByLabelText("Metric name").disabled).toBe(true);
  });

  // Round 3 UI: custom attributes often sit past the first catalog page.
  // One stable page per (search, cursor), as the real cache returns.
  const pagedCatalog = (lastPage) => {
    const pages = new Map();
    return (params) => {
      const key = `${params.search}:${params.cursor ?? ""}`;
      if (!pages.has(key)) {
        const page = Number(params.cursor || 0);
        const last = page >= lastPage;
        pages.set(key, {
          status: "success",
          result: {
            metrics: last ? catalog.result.metrics : [],
            next_cursor: last ? null : String(page + 1),
          },
        });
      }
      return pages.get(key);
    };
  };
  it("reads further catalog pages on its own until the project's fields show", async () => {
    state.catalogFor = pagedCatalog(3);
    renderPage();
    await waitFor(() => expect(state.catalogArgs.params.cursor).toBe("3"));
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Choose a source" }),
    );
    expect(screen.getByRole("option", { name: "Resolved" })).toBeTruthy();
    expect(state.catalogArgs.params.page_size).toBeLessThanOrEqual(50);
  });
  it("stops reading catalog pages at its cap and while a search is typed", async () => {
    state.catalogFor = pagedCatalog(Infinity);
    renderPage();
    await waitFor(() => expect(state.catalogArgs.params.cursor).toBe("4"));
    await act(async () => {});
    expect(state.catalogArgs.params.cursor).toBe("4");
    const source = screen.getByRole("combobox", { name: "Choose a source" });
    fireEvent.change(source, { target: { value: "res" } });
    await act(async () => {});
    expect(state.catalogArgs.params).toMatchObject({ search: "res" });
    expect(state.catalogArgs.params.cursor).toBeUndefined();
  });
  it("requests source catalog pages within the server's cursor maximum", () => {
    renderPage();
    // PROPERTY_CATALOG_MAX_PAGE_SIZE defaults to 50; larger pages are a 400.
    expect(state.catalogArgs.params).toMatchObject({ cursor_mode: true });
    expect(state.catalogArgs.params.page_size).toBeLessThanOrEqual(50);
  });
});
