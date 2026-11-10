import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import MetricProjectSelect from "../MetricProjectSelect";
import MetricList from "../MetricList";
import MetricDefinitionEditor from "../metric_definition_editor";
import { plainDefinitionSummary } from "../metric_calculation";
import { initialForm } from "../metricForm";
import wire from "./fixtures/committedResults.json";

vi.mock("../api", () => ({
  useMetricKpi: () => ({ readState: "ready", data: { current: null } }),
}));
const mock = vi.hoisted(() => ({ get: vi.fn(), projects: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: {
    derivedMetrics: {
      runs: (id) => `/metrics/${id}/runs`,
      results: (id) => `/runs/${id}/results`,
      evaluators: "/evaluators",
    },
  },
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: mock.projects,
}));
const metric = {
  id: wire.run.metric_id,
  name: "Resolution ratio",
  project_id: "project",
  current_version: 2,
  definition: wire.run.definition,
};
const projectName = "Support project with a deliberately long project name";
let client;
function mount(element) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}
function mountList() {
  return mount(
    <MetricList
      definitions={{ data: { results: [metric], next_cursor: null } }}
      scope={{ organizationId: "org", workspaceId: "ws" }}
      onSelect={vi.fn()}
    />,
  );
}
function Editor() {
  const [form, setForm] = useState(initialForm);
  return (
    <MetricDefinitionEditor
      form={form}
      projects={{
        data: { rows: [{ id: "project", name: projectName }], totalPages: 1 },
      }}
      onChange={(key, value) =>
        setForm((current) => ({ ...current, [key]: value }))
      }
      onReplace={setForm}
      onSave={vi.fn()}
      onValidate={vi.fn()}
      onCancel={vi.fn()}
    />
  );
}
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
beforeEach(() => {
  mock.projects.mockReset().mockResolvedValue({
    rows: [{ id: "project", name: projectName }],
    totalPages: 1,
  });
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: [wire.run], next_cursor: null } };
    if (url === `/runs/${wire.run.id}/results`) return { data: wire.old_page };
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

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

it("matches the sibling search field, shared default rows, project ellipsis/tooltip and exact terminal range", async () => {
  mountList();
  const search = screen.getByRole("textbox", {
    name: "Search metrics on this page",
  });
  expect(search).toHaveAttribute("placeholder", "Search this page");
  expect(search.closest(".MuiOutlinedInput-root")).toHaveStyle({
    height: "30px",
  });
  expect(screen.queryByText("Search this page")).not.toBeInTheDocument();
  const name = await screen.findByRole("button", { name: metric.name });
  // V2 rows use the shared two-line row: the value plus its returned interval,
  // or freshness plus recalculation progress, each held to one line.
  expect(name.closest('[role="row"]')).toHaveStyle({
    minHeight: "48px",
    maxHeight: "48px",
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

it("uses a single small project picker, full-width input labels, explicit steps and a plain summary with advanced details disclosed", async () => {
  mount(<Editor />);
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
