import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { createTheme } from "@mui/material/styles";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import NavItem from "src/components/nav-section/vertical/nav-item";
import DerivedMetricsView from "../DerivedMetricsView";
import SelectedRunSummary from "../SelectedRunSummary";
import { initialForm } from "../metricForm";
import { readMetricDraft, writeMetricDraft } from "../metric_draft";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  next: null,
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
      evaluators: "/evaluators",
    },
    dashboard: {
      create: "/dashboards",
      widgets: (id) => `/dashboards/${id}/widgets`,
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
  readObserveProjectPage: async () => ({
    rows: [{ id: "project", name: "Support" }],
    totalPages: 1,
  }),
}));
const metric = {
  id: wire.run.metric_id,
  name: "Average response time",
  description: "",
  project_id: "project",
  current_version: 2,
  definition: { ...initialForm(), version: 2 },
};
const run = {
  ...wire.run,
  definition: {
    ...initialForm(),
    version: 1,
    inputs: [
      {
        alias: "value",
        source: "span",
        field: "attribute_number",
        attribute_key: "quality",
        reducer: "latest",
      },
    ],
  },
};
const result = {
  ...wire.old_page.results[0],
  value: 1,
  valid_count: 2,
  unit_count: 2,
  numerator: 2,
  denominator: 2,
  uncertainty: { status: "unavailable", reason: "unstable_variance" },
};
const metricsPath = `/dashboard/evaluations/metrics?metric=${metric.id}&run=${run.id}`;
let client;
let names;
function mount(mode = "light") {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <nav aria-label="Actual shared navigation">
        <NavItem title="Tasks" path="/dashboard/tasks" depth={1} />
      </nav>
      <Routes>
        <Route
          path="/dashboard/evaluations/metrics/*"
          element={<DerivedMetricsView />}
        />
        <Route path="*" element={<h1>Tasks destination</h1>} />
      </Routes>
    </QueryClientProvider>,
    { theme: createTheme({ palette: { mode } }) },
  );
}
const click = (name) => {
  if (name === "New metric" && !screen.queryByRole("button", { name }))
    fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
  fireEvent.click(screen.getByRole("button", { name }));
};
const choose = async (label, option) => {
  fireEvent.mouseDown(
    screen.getByLabelText(label, { selector: '[role="combobox"]' }),
  );
  fireEvent.click(await screen.findByRole("option", { name: option }));
  await waitFor(() =>
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
  );
};
async function dirtyEditor() {
  await screen.findByRole("heading", { name: metric.name });
  click("Edit definition");
  fireEvent.change(screen.getByLabelText("Metric name"), {
    target: { value: "Unsaved question" },
  });
  await waitFor(() =>
    expect(readMetricDraft("org:ws")?.form.name).toBe("Unsaved question"),
  );
}
function storeRecovery() {
  writeMetricDraft("org:ws", {
    form: {
      ...initialForm(),
      name: "Original deferred question",
      project_id: "project",
      formula: "sum(value) + 7",
    },
    mode: "new",
    location: {
      pathname: "/dashboard/evaluations/metrics",
      search: "?from=recovery",
      hash: "#draft",
    },
    token: "original-owner",
    saveBlocked: false,
    conflict: false,
  });
}
beforeEach(() => {
  localStorage.clear();
  mock.get.mockReset();
  mock.post.mockReset();
  mock.next = null;
  mock.readOnly = false;
  names = [metric];
  window.history.replaceState({ idx: 0 }, "", metricsPath);
  mock.get.mockImplementation(async (url) => {
    if (url === "/metrics")
      return { data: { results: names, next_cursor: null } };
    if (url === `/metrics/${metric.id}`) return { data: metric };
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: [run], next_cursor: mock.next } };
    if (url === `/runs/${run.id}`) return { data: run };
    if (url === `/runs/${run.id}/results`)
      return { data: { ...wire.old_page, results: [result] } };
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    throw new Error(`Unexpected read ${url}`);
  });
  mock.post.mockImplementation(async (url) => ({
    data: url === "/dashboards" ? { result: { id: "dashboard" } } : {},
  }));
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
});

describe("polish r5 discriminating regressions", () => {
  it.each(["light", "dark"])(
    "ellipsizes a 144-character unbroken name with its full title in a compact table cell in %s",
    async (mode) => {
      const long = "M".repeat(144);
      names = [{ ...metric, name: long }];
      window.history.replaceState(
        { idx: 0 },
        "",
        "/dashboard/evaluations/metrics",
      );
      mount(mode);
      const row = await screen.findByRole("button", { name: long });
      expect(row).toHaveStyle({
        whiteSpace: "nowrap",
        minWidth: "0",
        height: "auto",
        width: "100%",
        overflow: "hidden",
      });
      expect(row).toHaveAttribute("title", long);
      expect(screen.getByRole("cell", { name: "v2" })).toBeInTheDocument();
    },
  );
  it("the real shared nav-item warns, Keep editing retains edits and Discard alone leaves", async () => {
    mount();
    await dirtyEditor();
    fireEvent.click(screen.getByRole("link", { name: "Tasks" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Discard unsaved changes?",
    });
    expect(window.location.pathname).toBe("/dashboard/evaluations/metrics");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Metric name")).toHaveValue(
      "Unsaved question",
    );
    fireEvent.click(screen.getByRole("link", { name: "Tasks" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Discard changes",
      }),
    );
    await screen.findByRole("heading", { name: "Tasks destination" });
    expect(readMetricDraft("org:ws")).toBeNull();
    expect(mock.post).not.toHaveBeenCalled();
  });
  it("browser Back restores the current entry before asking and replays only on Discard", async () => {
    window.history.replaceState(
      { idx: 0 },
      "",
      "/dashboard/tasks?from=back#origin",
    );
    window.history.pushState({ idx: 1 }, "", `${metricsPath}#editor`);
    mount();
    await dirtyEditor();
    await act(async () => window.history.back());
    const prompt = await screen.findByRole("dialog", {
      name: "Discard unsaved changes?",
    });
    expect(
      window.location.pathname + window.location.search + window.location.hash,
    ).toBe(`${metricsPath}#editor`);
    fireEvent.click(
      within(prompt).getByRole("button", { name: "Keep editing" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Metric name")).toHaveValue(
      "Unsaved question",
    );
    await act(async () => window.history.back());
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Discard changes",
      }),
    );
    await screen.findByRole("heading", { name: "Tasks destination" });
    expect(window.location.search + window.location.hash).toBe(
      "?from=back#origin",
    );
    await act(async () => window.history.forward());
    await screen.findByRole("heading", { name: metric.name });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it.each(["clean editor", "outside Metrics", "viewer"])(
    "leaves navigation unchanged for %s",
    async (state) => {
      if (state === "outside Metrics")
        window.history.replaceState({ idx: 0 }, "", "/dashboard/other");
      mock.readOnly = state === "viewer";
      mount();
      if (state !== "outside Metrics")
        await screen.findByRole("heading", { name: metric.name });
      fireEvent.click(screen.getByRole("link", { name: "Tasks" }));
      await screen.findByRole("heading", { name: "Tasks destination" });
      expect(window.location.pathname).toBe("/dashboard/tasks");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );
  it.each([
    ["Not now", "New metric"],
    ["Escape", "New metric"],
    ["Not now", "Edit definition"],
    ["Escape", "Edit definition"],
  ])(
    "%s then %s cannot overwrite the deferred draft",
    async (dismiss, action) => {
      storeRecovery();
      const stored = JSON.stringify(readMetricDraft("org:ws"));
      mount();
      const prompt = await screen.findByRole("dialog", {
        name: "Restore unsaved metric?",
      });
      if (dismiss === "Escape") fireEvent.keyDown(prompt, { key: "Escape" });
      else
        fireEvent.click(
          within(prompt).getByRole("button", { name: "Not now" }),
        );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      await screen.findByRole("heading", { name: metric.name });
      click(action);
      const resolution = await screen.findByRole("dialog", {
        name: "Restore unsaved metric?",
      });
      expect(resolution).toHaveTextContent("Original deferred question");
      expect(JSON.stringify(readMetricDraft("org:ws"))).toBe(stored);
      expect(screen.queryByLabelText("Metric name")).not.toBeInTheDocument();
      fireEvent.click(
        within(resolution).getByRole("button", { name: "Restore draft" }),
      );
      expect(await screen.findByLabelText("Metric name")).toHaveValue(
        "Original deferred question",
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      click("Continue");
      expect(screen.getByLabelText("Formula")).toHaveValue("sum(value) + 7");
      expect(mock.post).not.toHaveBeenCalled();
    },
  );
  it.each(["New metric", "Edit definition"])(
    "starts %s only after explicit discard of the deferred copy",
    async (action) => {
      storeRecovery();
      mount();
      fireEvent.click(
        within(await screen.findByRole("dialog")).getByRole("button", {
          name: "Not now",
        }),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      await screen.findByRole("heading", { name: metric.name });
      click(action);
      fireEvent.click(
        within(await screen.findByRole("dialog")).getByRole("button", {
          name: "Discard draft",
        }),
      );
      expect(await screen.findByLabelText("Metric name")).toHaveValue(
        action === "New metric" ? "" : metric.name,
      );
      expect(readMetricDraft("org:ws")).toBeNull();
    },
  );
  it("makes the value and plain meaning visible, with all technical evidence in one collapsed Details", () => {
    render(<SelectedRunSummary run={run} result={result} />);
    expect(screen.getByRole("heading", { name: "1.00" })).toBeVisible();
    expect(
      screen.getByText(
        /Average of quality across 2 sessions, from.*local time/,
      ),
    ).toBeVisible();
    expect(screen.getByText(/Point estimate only/)).toBeVisible();
    const details = screen.getByText("Details").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(
      screen.getByText(/Interval detail: unstable variance/).closest("details"),
    ).toBe(details);
    expect(screen.getByText(/invalid probability/).closest("details")).toBe(
      details,
    );
    expect(screen.getByText(/Window \(UTC\):.*UTC/).closest("details")).toBe(
      details,
    );
    expect(
      screen.getByText(/Saved definition · version/).closest("details"),
    ).toBe(details);
    expect(document.querySelectorAll("details")).toHaveLength(1);
  });
  it("compact context keeps meaning and an explicit Details affordance, with separated evidence sentences", () => {
    render(<SelectedRunSummary compact run={run} result={result} />);
    expect(
      screen.getByText(/Average of quality across 2 sessions/),
    ).toBeVisible();
    const details = screen.getByText("Details").closest("details");
    expect(details).not.toHaveAttribute("open");
    const totals = screen.getByText(/denominator total: 2\./);
    expect(totals.tagName).toBe("SPAN");
    expect(totals.nextElementSibling.tagName).toBe("SPAN");
    expect(getComputedStyle(totals.parentElement).gap).not.toBe("");
    expect(totals.nextElementSibling).toHaveTextContent(
      /^Totals are formula contributions/,
    );
    expect(totals.closest("details")).toBe(details);
    expect(screen.queryByRole("heading", { level: 2 })).not.toBeInTheDocument();
  });
  it("uses guided labels and searchable projects without changing numeric input payloads", async () => {
    mount();
    click("New metric");
    expect(screen.getByRole("combobox", { name: "Project" })).toBeVisible();
    expect(screen.queryByLabelText("Search projects")).not.toBeInTheDocument();
    expect(screen.queryByText("Find another project")).not.toBeInTheDocument();
    expect(screen.getByLabelText("What to summarize")).toHaveTextContent(
      "Span field",
    );
    await choose("Span value", "attribute number");
    fireEvent.change(screen.getByLabelText("Attribute name"), {
      target: { value: "quality" },
    });
    await choose("If a session has several values, use", "mean");
    await waitFor(() =>
      expect(readMetricDraft("org:ws")?.form.inputs[0]).toMatchObject({
        source: "span",
        field: "attribute_number",
        attribute_key: "quality",
        reducer: "mean",
      }),
    );
    expect(
      screen.getByRole("button", { name: "Advanced configuration" }),
    ).toBeInTheDocument();
  });
  it.each(["light", "dark"])(
    "bounds Data, Calculation and Review widths and keeps a full-height surface in %s",
    async (mode) => {
      mount(mode);
      click("New metric");
      const editor = screen.getByRole("region", {
        name: "Metric definition editor",
      });
      expect(editor).toHaveStyle({
        minWidth: "0",
        width: "100%",
        overflowWrap: "anywhere",
      });
      expect(editor).toHaveStyle({ minHeight: "0", flex: "1" });
      expect(screen.queryByLabelText("Saved metrics")).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Metric name"), {
        target: { value: "Q".repeat(144) },
      });
      await choose("Project", "Support");
      click("Continue");
      expect(
        screen.getByRole("heading", { name: "Calculation" }),
      ).toBeVisible();
      click("Continue");
      expect(screen.getByRole("heading", { name: "Review" })).toBeVisible();
      expect(editor).toHaveStyle({ minWidth: "0", width: "100%" });
    },
  );
  it("puts technical controls inside Details and keeps secondary actions grouped, with no orphan history links", async () => {
    mount();
    await screen.findByRole("heading", { name: "1.00" });
    const details = screen.getByText("Details").closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("tab", { name: "Contributors" }));
    expect(screen.getByLabelText("Group results by")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Result" }));
    expect(
      screen.getByRole("button", { name: "Create dashboard" }),
    ).toHaveClass("MuiButton-outlined", "MuiButton-sizeSmall");
    expect(
      screen.queryByRole("button", { name: "Refresh run status" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "First runs" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Past runs pages")).not.toBeInTheDocument();
  });
  it("writes a readable dashboard description and keeps exact run identity only in widget config", async () => {
    mount();
    await screen.findByRole("heading", { name: "1.00" });
    click("Create dashboard");
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Dashboard name");
    fireEvent.change(input, { target: { value: "Quality overview" } });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create dashboard" }),
    );
    await waitFor(() =>
      expect(
        mock.post.mock.calls.some(
          ([url]) => url === "/dashboards/dashboard/widgets",
        ),
      ).toBe(true),
    );
    const description = mock.post.mock.calls.find(
      ([url]) => url === "/dashboards",
    )[1].description;
    expect(description).toContain(metric.name);
    expect(description).toContain("UTC");
    expect(description).not.toContain(run.id);
    expect(
      mock.post.mock.calls.find(([url]) => url.endsWith("/widgets"))[1]
        .query_config.metrics[0].derived_run_id,
    ).toBe(run.id);
  });
});
