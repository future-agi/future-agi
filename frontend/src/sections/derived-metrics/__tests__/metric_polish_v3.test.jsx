import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import DerivedMetricsView from "../DerivedMetricsView";
import MetricProjectSelect from "../MetricProjectSelect";
import MetricRunHistory from "../MetricRunHistory";
import Evals from "src/pages/dashboard/evals/Evals";
import { plainDefinitionSummary } from "../metric_calculation";
import { initialForm } from "../metricForm";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({ get: vi.fn(), projects: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: {
    derivedMetrics: {
      list: "/metrics",
      detail: (id) => `/metrics/${id}`,
      runs: (id) => `/metrics/${id}/runs`,
      run: (id) => `/runs/${id}`,
      results: (id) => `/runs/${id}/results`,
      evaluators: "/evaluators",
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
  default: () => ({ isReadOnly: false }),
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: mock.projects,
}));
vi.mock("src/sections/evals/components/EvalsListView", () => ({
  default: () => <div>Evaluator list</div>,
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
const metric = {
  id: wire.run.metric_id,
  name: "Resolution ratio",
  project_id: "project",
  current_version: 2,
  definition: wire.run.definition,
};
const projectName = "Support project with a deliberately long project name";
let client;
let currentRun;
let page;
function mount(element = <DerivedMetricsView />) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const tab = (name) => fireEvent.click(screen.getByRole("tab", { name }));
async function detail() {
  window.history.replaceState(
    { idx: 0 },
    "",
    `/dashboard/evaluations/metrics/${metric.id}?run=${wire.run.id}`,
  );
  mount();
  await screen.findByRole("heading", { name: "0.50" });
}
it("a recovered project's changing catalog cannot overwrite the search being typed or change the selected project", () => {
  const changed = vi.fn();
  function RecoveredPicker() {
    const [search, setSearch] = useState("");
    return (
      <MetricProjectSelect
        value="saved-project"
        projects={{
          data: {
            rows: search
              ? [{ id: "other", name: "Other match" }]
              : [{ id: "saved-project", name: "Saved support" }],
            totalPages: 1,
          },
        }}
        search={search}
        page={0}
        onSearch={setSearch}
        onPage={vi.fn()}
        onChange={changed}
      />
    );
  }
  mount(<RecoveredPicker />);
  const input = screen.getByRole("combobox", { name: "Project" });
  expect(input).toHaveValue("Saved support");
  fireEvent.change(input, { target: { value: "Sup" } });
  expect(input).toHaveValue("Sup");
  expect(changed).not.toHaveBeenCalled();
  expect(screen.getByRole("option", { name: "Other match" })).toBeVisible();
  expect(
    screen.queryByRole("option", { name: "Saved project" }),
  ).not.toBeInTheDocument();
});

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({ idx: 0 }, "", "/dashboard/evaluations/metrics");
  currentRun = structuredClone(wire.run);
  page = structuredClone(wire.old_page);
  mock.projects.mockReset();
  mock.projects.mockResolvedValue({
    rows: [{ id: "project", name: projectName }],
    totalPages: 1,
  });
  mock.get.mockReset();
  mock.get.mockImplementation(async (url) => {
    if (url === "/metrics")
      return { data: { results: [metric], next_cursor: null } };
    if (url === `/metrics/${metric.id}`) return { data: metric };
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: [currentRun], next_cursor: null } };
    if (url === `/runs/${wire.run.id}`) return { data: currentRun };
    if (url === `/runs/${wire.run.id}/results`) return { data: page };
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
});

it("matches the sibling search field, default 44px rows, project ellipsis/tooltip and exact terminal range", async () => {
  mount();
  const search = screen.getByRole("textbox", {
    name: "Search metrics on this page",
  });
  expect(search).toHaveAttribute("placeholder", "Search this page");
  expect(search.closest(".MuiOutlinedInput-root")).toHaveStyle({
    height: "30px",
  });
  expect(screen.queryByText("Search this page")).not.toBeInTheDocument();
  const name = await screen.findByRole("button", { name: metric.name });
  expect(name.closest('[role="row"]')).toHaveStyle({
    minHeight: "44px",
    maxHeight: "44px",
  });
  const project = await screen.findByText(projectName);
  expect(project).toHaveStyle({
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
  fireEvent.mouseOver(project);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(projectName);
  expect(screen.getByText("1–1 of 1")).toBeInTheDocument();
  expect(screen.getByLabelText("Rows per page")).toHaveValue("25");
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "First page" }),
  ).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: "not a metric" } });
  expect(
    await screen.findByText("No metrics match your search"),
  ).toBeInTheDocument();
});

it("paginates with actual opaque cursors, preserves previous pages and resets cursor on page-size change without inventing a total", async () => {
  const read = mock.get.getMockImplementation();
  mock.get.mockImplementation(async (url, config) => {
    if (url === "/metrics")
      return {
        data: {
          results: config.params.before ? [] : [metric],
          next_cursor: config.params.before ? null : "opaque-next",
        },
      };
    return read(url, config);
  });
  mount();
  await screen.findByRole("button", { name: metric.name });
  expect(screen.getByText("1–25 of more than 25")).toBeInTheDocument();
  click("Next page");
  await waitFor(() =>
    expect(mock.get).toHaveBeenCalledWith(
      "/metrics",
      expect.objectContaining({ params: { limit: 25, before: "opaque-next" } }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled(),
  );
  expect(screen.getAllByText("No metrics on this page").length).toBeGreaterThan(
    0,
  );
  expect(screen.queryByText("26–25 of 25")).not.toBeInTheDocument();
  click("Previous page");
  await screen.findByRole("button", { name: metric.name });
  fireEvent.change(screen.getByLabelText("Rows per page"), {
    target: { value: "10" },
  });
  await waitFor(() =>
    expect(mock.get).toHaveBeenCalledWith(
      "/metrics",
      expect.objectContaining({ params: { limit: 10 } }),
    ),
  );
  expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
});

it("uses the same subtitle on both Evals tabs", async () => {
  const view = mount(<Evals />);
  const subtitle = screen.getByText(
    "Create and manage your evaluations",
  ).textContent;
  view.unmount();
  client.clear();
  mount();
  expect(screen.getByText(subtitle)).toBeInTheDocument();
});

it("Past runs is a five-column table, not a repeated result or dropdown, and choosing a row selects its frozen run on Result", async () => {
  await detail();
  tab("Past runs");
  const table = await screen.findByRole("table", { name: "Past metric runs" });
  for (const name of ["Window (UTC)", "Status", "Value", "Version", "Created"])
    expect(within(table).getByRole("columnheader", { name })).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "Selected saved run" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("combobox", { name: "Past runs" }),
  ).not.toBeInTheDocument();
  expect(await within(table).findByText("0.5")).toBeVisible();
  fireEvent.click(within(table).getByRole("button"));
  expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    await screen.findByRole("heading", { name: "0.50" }),
  ).toBeInTheDocument();
  expect(window.location.search).toContain(`run=${wire.run.id}`);
  expect(
    screen.getByRole("region", { name: "Selected saved run" }),
  ).toHaveTextContent("version 1");
});

it("choosing a different history row changes the run URL and shows that run's version and committed value", async () => {
  const other = {
    ...wire.run,
    id: "00000000-0000-0000-0000-000000000009",
    metric_version: 3,
    result_id: wire.new_page.result_id,
    window_start: "2026-10-20T00:00:00.123456Z",
  };
  const read = mock.get.getMockImplementation();
  mock.get.mockImplementation(async (url, config) => {
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: [currentRun, other], next_cursor: null } };
    if (url === `/runs/${other.id}`) return { data: other };
    if (url === `/runs/${other.id}/results`) return { data: wire.new_page };
    return read(url, config);
  });
  await detail();
  tab("Past runs");
  const history = await screen.findByRole("table", {
    name: "Past metric runs",
  });
  const otherRow = within(history).getByText("v3").closest("tr");
  fireEvent.click(within(otherRow).getByRole("button"));
  await screen.findByRole("heading", { name: "0.00" });
  expect(window.location.search).toContain(`run=${other.id}`);
  expect(
    screen.getByRole("region", { name: "Selected saved run" }),
  ).toHaveTextContent("version 3");
  expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

it.each([0, null, "mismatch"])(
  "history retains committed zero/null/identity semantics (%s)",
  async (value) => {
    if (value === "mismatch")
      page.result_id = "00000000-0000-0000-0000-000000000099";
    else page.results[0].value = value;
    mount(
      <MetricRunHistory
        runs={[currentRun]}
        metricId={metric.id}
        scope="org:ws"
        onSelect={vi.fn()}
      />,
    );
    const table = screen.getByRole("table", { name: "Past metric runs" });
    if (value === "mismatch") {
      expect(
        await within(table).findByRole("button", { name: "Retry value" }),
      ).toBeEnabled();
      expect(within(table).queryByText("0.5")).not.toBeInTheDocument();
    } else
      expect(
        await within(table).findByText(value === null ? "No value" : "0"),
      ).toBeVisible();
  },
);

it("keeps dashboard and monitor as small outlined header actions, removes the duplicate link and states numeric ratio contributions", async () => {
  await detail();
  for (const name of ["Create dashboard", "Create monitor"]) {
    const action = screen.getByRole("button", { name });
    expect(action).toHaveClass("MuiButton-outlined", "MuiButton-sizeSmall");
    expect(action.closest("div")).toBe(
      screen.getByText("v2").closest(".MuiChip-root").parentElement,
    );
  }
  expect(
    screen.queryByRole("link", { name: "Inspect contributors" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(
      /0.50 = 1 ÷ 2 \(numerator total ÷ denominator total\) across 2 sessions/,
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Refresh run status" }),
  ).not.toBeInTheDocument();
});

it.each(["queued", "running", "failed"])(
  "offers status refresh only for active runs (%s), with an explicit saved-read retry when needed",
  async (status) => {
    currentRun.status = status;
    await detail();
    if (status === "failed")
      expect(
        screen.queryByRole("button", { name: "Refresh run status" }),
      ).not.toBeInTheDocument();
    else
      expect(
        screen.getByRole("button", { name: "Refresh run status" }),
      ).toBeEnabled();
  },
);

it("a completed result read failure still has a working identity-safe retry", async () => {
  page.result_id = "00000000-0000-0000-0000-000000000099";
  window.history.replaceState(
    { idx: 0 },
    "",
    `/dashboard/evaluations/metrics/${metric.id}?run=${wire.run.id}`,
  );
  mount();
  const retry = await screen.findByRole("button", {
    name: "Retry saved result",
  });
  expect(
    screen.queryByRole("button", { name: "Refresh run status" }),
  ).not.toBeInTheDocument();
  page = structuredClone(wire.old_page);
  fireEvent.click(retry);
  expect(
    await screen.findByRole("heading", { name: "0.50" }),
  ).toBeInTheDocument();
});

it("uses a single small project picker, full-width input labels, explicit steps and a plain summary with advanced details disclosed", async () => {
  mount();
  click("New metric");
  expect(
    screen.queryByText(/A recovery draft is kept/),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Step 1 of 3 · Data")).toBeVisible();
  const project = screen.getByRole("combobox", { name: "Project" });
  expect(screen.queryByLabelText("Search projects")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Previous projects" }),
  ).not.toBeInTheDocument();
  expect(project.closest(".MuiInputBase-root")).toHaveClass(
    "MuiInputBase-sizeSmall",
  );
  for (const label of [
    "What to summarize",
    "If a session has several values, use",
  ])
    expect(
      screen.getByLabelText(label).closest(".MuiFormControl-root"),
    ).toHaveClass("MuiFormControl-fullWidth");
  expect(
    screen.getByText("Average of the latest latency ms value per session."),
  ).toBeVisible();
  expect(screen.queryByText(/1 input ·/)).not.toBeInTheDocument();
  click("Advanced configuration");
  expect(screen.getByText(/1 input ·/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Metric name"), {
    target: { value: "Question" },
  });
  expect(
    screen.queryByText(/A recovery draft is kept/),
  ).not.toBeInTheDocument();
});

it("the single project picker searches remotely, loads more in its popup and retains the selected project across catalog pages", async () => {
  const search = vi.fn();
  const onChange = vi.fn();
  function Picker() {
    const [pageNumber, setPage] = useState(0);
    const [value, setValue] = useState("");
    return (
      <MetricProjectSelect
        value={value}
        projects={{
          data: {
            rows: pageNumber
              ? [{ id: "two", name: "Second project" }]
              : [{ id: "one", name: "First project" }],
            totalPages: 2,
          },
        }}
        search=""
        page={pageNumber}
        onSearch={search}
        onPage={setPage}
        onChange={(id) => {
          setValue(id);
          onChange(id);
        }}
      />
    );
  }
  mount(<Picker />);
  const input = screen.getByRole("combobox", { name: "Project" });
  fireEvent.change(input, { target: { value: "Sup" } });
  expect(search).toHaveBeenCalledWith("Sup");
  fireEvent.click(
    await screen.findByRole("option", { name: "Load more projects" }),
  );
  fireEvent.click(
    await screen.findByRole("option", { name: "Second project" }),
  );
  expect(input).toHaveValue("Second project");
  expect(onChange).toHaveBeenCalledExactlyOnceWith("two");
});

it("plain summaries name the actual reducer without changing calculation inputs", () => {
  const form = {
    ...initialForm(),
    inputs: [
      {
        ...initialForm().inputs[0],
        field: "attribute_number",
        attribute_key: "resolved",
      },
    ],
  };
  expect(plainDefinitionSummary(form)).toBe(
    "Average of the latest resolved value per session.",
  );
  expect(
    plainDefinitionSummary({
      ...form,
      weighting: "inverse_probability",
      filters: [{ field: "model", value: "chosen" }],
    }),
  ).toContain("Inverse-probability weighted. With definition filters.");
});
