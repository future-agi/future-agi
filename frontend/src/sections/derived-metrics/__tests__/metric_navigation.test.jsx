import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import { useLocation } from "react-router-dom";
import DerivedMetricsView from "../DerivedMetricsView";
import LegacyMetricsRedirect from "src/routes/components/legacy_metrics_redirect";
import EvalsSectionTabs from "src/sections/evals/components/evals_section_tabs";
import Evals from "src/pages/dashboard/evals/Evals";
import { HelmetProvider } from "react-helmet-async";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  readOnly: false,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get, post: mock.post },
  endpoints: {
    derivedMetrics: {
      list: "/metrics",
      detail: (id) => `/metrics/${id}`,
      runs: (id) => `/metrics/${id}/runs`,
      run: (id) => `/runs/${id}`,
      results: (id) => `/runs/${id}/results`,
    },
  },
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws", isReady: true }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org" }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: mock.readOnly }),
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => ({ rows: [], totalPages: 0 }),
}));
vi.mock("src/sections/evals/components/EvalsListView", () => ({
  default: () => <div>Existing evaluator catalog</div>,
}));
const metric = {
  id: wire.run.metric_id,
  project_id: "00000000-0000-0000-0000-000000000002",
  name: "Saved metric",
  description: "",
  current_version: 2,
  definition: { ...wire.run.definition, version: 2 },
};
const other = {
  ...metric,
  id: "00000000-0000-0000-0000-000000000099",
  name: "No runs metric",
};
const locationPath = () =>
  `${window.location.pathname}${window.location.search}${window.location.hash}`;
function Location() {
  const location = useLocation();
  return (
    <output aria-label="Location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  );
}
let client;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DerivedMetricsView />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  mock.get.mockReset();
  mock.post.mockReset();
  mock.readOnly = false;
  window.history.replaceState({}, "", "/dashboard/evaluations/metrics");
  mock.get.mockImplementation(async (url) => {
    if (url === "/metrics")
      return { data: { results: [metric, other], next_cursor: null } };
    if (url === `/metrics/${metric.id}`) return { data: metric };
    if (url === `/metrics/${other.id}`) return { data: other };
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: [wire.run], next_cursor: null } };
    if (url === `/metrics/${other.id}/runs`)
      return { data: { results: [], next_cursor: null } };
    if (url === `/runs/${wire.run.id}`) return { data: wire.run };
    if (url === `/runs/${wire.run.id}/results`) return { data: wire.old_page };
    throw new Error(`Unknown offline read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

async function expectCommittedValue() {
  // Wait on the independently verified query before the same DOM value assertion.
  // Avoid repeatedly walking the complete themed tree while React Query is still
  // notifying observers. This does not change any timeout or result assertion.
  await waitFor(() =>
    expect(
      client.getQueryData([
        "metric-results",
        "org:ws",
        wire.run.id,
        wire.old_page.result_id,
        "overall",
        "",
      ]),
    ).toEqual(wire.old_page),
  );
  expect(
    await screen.findByRole("heading", { name: "0.50" }),
  ).toBeInTheDocument();
}

describe("metric and run URL identity", () => {
  it("restores an exact off-first-page metric/run after reload and opens the preserved evidence hash", async () => {
    const original = mock.get.getMockImplementation();
    mock.get.mockImplementation((url, config) =>
      url === "/metrics"
        ? Promise.resolve({ data: { results: [], next_cursor: "next" } })
        : original(url, config),
    );
    const url = `/dashboard/evaluations/metrics?project=preserved&metric=${metric.id}&run=${wire.run.id}#saved-run-results`;
    window.history.replaceState({}, "", url);
    const view = mount();
    fireEvent.click(await screen.findByRole("tab", { name: "Result" }));
    expect(
      await screen.findByRole("region", { name: "Selected saved run" }),
    ).toHaveTextContent("version 1");
    await expectCommittedValue();
    expect(mock.get).toHaveBeenCalledWith(
      `/metrics/${metric.id}`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await waitFor(() =>
      expect(document.getElementById("saved-run-results")).toHaveAttribute(
        "open",
      ),
    );
    expect(locationPath()).toBe(url);
    view.unmount();
    client.clear();
    mount();
    fireEvent.click(await screen.findByRole("tab", { name: "Result" }));
    await expectCommittedValue();
    expect(locationPath()).toBe(url);
    expect(mock.post).not.toHaveBeenCalled();
  });
  it("back and forward restore the selected metric and immutable run, not another run", async () => {
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Saved metric" }),
    );
    await expectCommittedValue();
    await waitFor(() =>
      expect(window.location.search).toContain(`run=${wire.run.id}`),
    );
    const savedUrl = locationPath();
    fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "No runs metric" }),
    );
    await screen.findByText("No saved results");
    expect(
      screen.queryByRole("region", { name: "Selected saved run" }),
    ).not.toBeInTheDocument();
    await act(async () => window.history.go(-2));
    await expectCommittedValue();
    expect(locationPath()).toBe(savedUrl);
    await act(async () => window.history.go(2));
    await screen.findByText("No saved results");
    expect(window.location.pathname).toContain(other.id);
  });
  it.each([
    "?metric=not-a-uuid&run=bad",
    `?run=${wire.run.id}`,
    `?metric=${metric.id}&run=bad`,
  ])(
    "rejects malformed/orphan selection %s without issuing detail/run reads",
    async (search) => {
      window.history.replaceState(
        {},
        "",
        `/dashboard/evaluations/metrics${search}`,
      );
      mount();
      expect(
        await screen.findByText(/Invalid metric or run link/),
      ).toBeInTheDocument();
      expect(mock.get.mock.calls.every(([url]) => url === "/metrics")).toBe(
        true,
      );
      expect(mock.post).not.toHaveBeenCalled();
    },
  );
  it("does not substitute another run for an unavailable or mismatched deep link", async () => {
    window.history.replaceState(
      { idx: 0 },
      "",
      `/dashboard/evaluations/metrics?metric=${metric.id}&run=${wire.run.id}`,
    );
    const original = mock.get.getMockImplementation();
    mock.get.mockImplementation((url, config) =>
      url === `/runs/${wire.run.id}`
        ? Promise.resolve({ data: { ...wire.run, metric_id: other.id } })
        : original(url, config),
    );
    mount();
    await screen.findByText(/Selected run identity could not be verified/);
    expect(
      screen.queryByRole("region", { name: "Selected saved run" }),
    ).not.toBeInTheDocument();
    expect(mock.get.mock.calls.some(([url]) => url.endsWith("/results"))).toBe(
      false,
    );
    expect(window.location.search).toContain(wire.run.id);
  });
  it("allows explicit no-run selection without resetting to the newest run", async () => {
    window.history.replaceState(
      { idx: 0 },
      "",
      `/dashboard/evaluations/metrics?metric=${metric.id}&run=`,
    );
    mount();
    await screen.findByText("Select a saved run to inspect its result.");
    expect(
      screen.queryByRole("region", { name: "Selected saved run" }),
    ).not.toBeInTheDocument();
    expect(mock.get.mock.calls.some(([url]) => url.startsWith("/runs/"))).toBe(
      false,
    );
  });
  it("closes the short run dialog with Escape and restores keyboard focus to its trigger", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(
      await screen.findByRole("button", { name: "Saved metric" }),
    );
    const trigger = await screen.findByRole("button", { name: "Run metric" });
    await user.click(trigger);
    expect(
      screen.getByRole("dialog", { name: "Run metric" }),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
    expect(mock.post).not.toHaveBeenCalled();
  });
  it("rejects an unavailable off-page metric without substituting the first visible definition", async () => {
    const id = "00000000-0000-0000-0000-000000000088";
    window.history.replaceState(
      { idx: 0 },
      "",
      `/dashboard/evaluations/metrics?metric=${id}&run=${wire.run.id}`,
    );
    const original = mock.get.getMockImplementation();
    mock.get.mockImplementation((url, config) =>
      url === `/metrics/${id}`
        ? Promise.reject({ statusCode: 404 })
        : original(url, config),
    );
    mount();
    await screen.findByText(/This metric is unavailable in this workspace/);
    expect(
      screen.queryByRole("region", { name: "Selected saved run" }),
    ).not.toBeInTheDocument();
    expect(mock.get.mock.calls.some(([url]) => url.startsWith("/runs/"))).toBe(
      false,
    );
    expect(window.location.search).toContain(id);
  });
  it("preserves a new draft on browser Back and honors the original destination after discard", async () => {
    window.history.replaceState(
      { idx: 0 },
      "",
      `/dashboard/evaluations/metrics?metric=${metric.id}&run=${wire.run.id}`,
    );
    window.history.pushState({ idx: 1 }, "", "/dashboard/evaluations/metrics");
    mount();
    fireEvent.click(screen.getByRole("button", { name: "New metric" }));
    fireEvent.change(screen.getByLabelText("Metric name"), {
      target: { value: "New unsaved draft" },
    });
    await act(async () => window.history.back());
    await screen.findByRole("dialog", { name: "Discard unsaved changes?" });
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(screen.getByLabelText("Metric name")).toHaveValue(
      "New unsaved draft",
    );
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await screen.findByRole("heading", { name: "Saved metric" });
    expect(window.location.search).toContain(metric.id);
    expect(mock.post).not.toHaveBeenCalled();
  });
  it("asks before discarding edits when switching metrics", async () => {
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Saved metric" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit definition" }),
    );
    fireEvent.change(screen.getByLabelText("Metric name"), {
      target: { value: "Unsaved draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
    expect(
      screen.getByRole("dialog", { name: "Discard unsaved changes?" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Metric name")).toHaveValue("Unsaved draft");
    fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "No runs metric" }),
    );
    await screen.findByText("No saved results");
    expect(mock.post).not.toHaveBeenCalled();
  });
});

describe("Evals placement and compatibility", () => {
  it("mounts Metrics navigation on the actual evaluator landing and keeps its catalog", () => {
    window.history.replaceState({}, "", "/dashboard/evaluations");
    render(
      <HelmetProvider>
        <Evals />
      </HelmetProvider>,
    );
    expect(screen.getByText("Existing evaluator catalog")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Evaluators" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Metrics" }));
    expect(window.location.pathname).toBe("/dashboard/evaluations/metrics");
  });
  it("legacy replace redirect keeps all query parameters and hash", async () => {
    window.history.replaceState(
      { idx: 0 },
      "",
      `/dashboard/derived-metrics?metric=${metric.id}&run=${wire.run.id}&project=kept#saved-run-results`,
    );
    render(
      <>
        <LegacyMetricsRedirect />
        <Location />
      </>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Location")).toHaveTextContent(
        `/dashboard/evaluations/metrics?metric=${metric.id}&run=${wire.run.id}&project=kept#saved-run-results`,
      ),
    );
  });
  it.each(["usage", "groups"])(
    "shared navigation preserves the existing %s destination",
    (destination) => {
      render(<EvalsSectionTabs />);
      fireEvent.click(
        screen.getByRole("tab", {
          name: destination === "usage" ? "Usage" : "Groups",
        }),
      );
      expect(window.location.pathname).toBe(
        `/dashboard/evaluations/${destination}`,
      );
    },
  );
});
