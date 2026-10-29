import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import MetricProjectSelect from "../MetricProjectSelect";
import MetricRunHistory from "../MetricRunHistory";
import MetricList from "../MetricList";
import MetricContributors from "../MetricContributors";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({ get: vi.fn(), projects: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: {
    derivedMetrics: {
      runs: (id) => `/metrics/${id}/runs`,
      run: (id) => `/runs/${id}`,
      results: (id) => `/runs/${id}/results`,
      contributors: (id) => `/runs/${id}/contributors`,
    },
  },
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: mock.projects,
}));
let client;
function mount(element, mode = "light") {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
    { theme: createTheme({ palette: { mode } }) },
  );
}
beforeEach(() => {
  mock.get.mockReset();
  mock.projects.mockReset();
  mock.projects.mockResolvedValue({
    rows: [{ id: "project", name: "Support" }],
    totalPages: 1,
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

it.each(["saved", "chosen"])(
  "reconciles an unchosen project search on blur without changing the %s project ID, and still permits a fresh selection",
  async (source) => {
    const changed = vi.fn();
    function Picker() {
      const [value, setValue] = useState(source === "saved" ? "alpha" : "");
      const [search, setSearch] = useState("");
      return (
        <>
          <MetricProjectSelect
            value={value}
            projects={{
              data: {
                rows: search
                  ? [{ id: "beta", name: "Beta project" }]
                  : [
                      { id: "alpha", name: "Alpha project" },
                      { id: "beta", name: "Beta project" },
                    ],
                totalPages: 1,
              },
            }}
            search={search}
            page={0}
            onSearch={setSearch}
            onChange={(id) => {
              setValue(id);
              changed(id);
            }}
          />
          <output data-testid="project-id">{value}</output>
        </>
      );
    }
    mount(<Picker />);
    const input = screen.getByRole("combobox", { name: "Project" });
    if (source === "chosen") {
      fireEvent.mouseDown(input);
      fireEvent.click(
        await screen.findByRole("option", { name: "Alpha project" }),
      );
    }
    expect(input).toHaveValue("Alpha project");
    changed.mockClear();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Beta project" } });
    expect(input).toHaveValue("Beta project");
    expect(screen.getByTestId("project-id")).toHaveTextContent("alpha");
    fireEvent.blur(input);
    await waitFor(() => expect(input).toHaveValue("Alpha project"));
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByTestId("project-id")).toHaveTextContent("alpha");
    fireEvent.mouseDown(input);
    fireEvent.click(
      await screen.findByRole("option", { name: "Beta project" }),
    );
    expect(input).toHaveValue("Beta project");
    expect(screen.getByTestId("project-id")).toHaveTextContent("beta");
    expect(changed).toHaveBeenCalledExactlyOnceWith("beta");
  },
);

function history() {
  const other = { ...wire.run, id: "00000000-0000-0000-0000-000000000009" };
  mount(
    <MetricRunHistory
      runs={[wire.run, other]}
      selectedRun={other}
      metricId={wire.run.metric_id}
      scope="org:workspace"
      onSelect={vi.fn()}
    />,
  );
  const table = screen.getByRole("table", { name: "Past metric runs" });
  return within(table).getAllByRole("row")[1];
}
const reads = (url) => mock.get.mock.calls.filter(([path]) => path === url);

it("Retry value reconciles a nonselected history run's new committed identity without changing the selected run", async () => {
  let refreshed = false;
  const runUrl = `/runs/${wire.run.id}`;
  mock.get.mockImplementation(async (url) => {
    if (url === runUrl)
      return {
        data: {
          ...wire.run,
          result_id: refreshed ? wire.new_page.result_id : wire.run.result_id,
        },
      };
    if (url === `${runUrl}/results`) return { data: wire.new_page };
    if (url.endsWith("/results")) return { data: wire.old_page };
    throw new Error(`Unexpected read ${url}`);
  });
  const row = history();
  const retry = await within(row).findByRole("button", { name: "Retry value" });
  expect(within(row).queryByText("0")).not.toBeInTheDocument();
  const before = reads(runUrl).length;
  refreshed = true;
  fireEvent.click(retry);
  expect(await within(row).findByText("0")).toBeVisible();
  expect(reads(runUrl)).toHaveLength(before + 1);
  expect(row).not.toHaveClass("Mui-selected");
  expect(
    screen
      .getByRole("table", { name: "Past metric runs" })
      .querySelectorAll(".Mui-selected"),
  ).toHaveLength(1);
  const data = client
    .getQueryCache()
    .findAll({ queryKey: ["metric-history-value"] })
    .map((query) => query.state.data);
  const recovered = data.find((entry) => entry?.run.id === wire.run.id);
  expect(recovered.run.result_id).toBe(wire.new_page.result_id);
  expect(recovered.page.result_id).toBe(recovered.run.result_id);
});

it("bounds history reconciliation to one identity reread per attempt and never displays a continuously mismatched value", async () => {
  const runUrl = `/runs/${wire.run.id}`;
  mock.get.mockImplementation(async (url) => {
    if (url === runUrl) return { data: wire.run };
    if (url === `${runUrl}/results`) return { data: wire.new_page };
    if (url.endsWith("/results")) return { data: wire.old_page };
    throw new Error(`Unexpected read ${url}`);
  });
  const row = history();
  const retry = await within(row).findByRole("button", { name: "Retry value" });
  expect(reads(runUrl)).toHaveLength(1);
  expect(reads(`${runUrl}/results`)).toHaveLength(2);
  fireEvent.click(retry);
  await waitFor(() => expect(reads(runUrl)).toHaveLength(2));
  await waitFor(() => expect(client.isFetching()).toBe(0));
  expect(reads(`${runUrl}/results`)).toHaveLength(4);
  expect(
    within(row).getByRole("button", { name: "Retry value" }),
  ).toBeEnabled();
  expect(within(row).queryByText("0")).not.toBeInTheDocument();
  expect(within(row).queryByText("0.5")).not.toBeInTheDocument();
});

it.each(["id", "metric_id"])(
  "refuses a refreshed history run with a different %s even if its result would match",
  async (field) => {
    const runUrl = `/runs/${wire.run.id}`;
    mock.get.mockImplementation(async (url) => {
      if (url === runUrl)
        return {
          data: {
            ...wire.run,
            [field]: "00000000-0000-0000-0000-000000000099",
            result_id: wire.new_page.result_id,
          },
        };
      if (url === `${runUrl}/results`) return { data: wire.new_page };
      if (url.endsWith("/results")) return { data: wire.old_page };
      throw new Error(`Unexpected read ${url}`);
    });
    const row = history();
    await within(row).findByRole("button", { name: "Retry value" });
    expect(reads(runUrl)).toHaveLength(1);
    expect(reads(`${runUrl}/results`)).toHaveLength(1);
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
  },
);

const metric = {
  id: wire.run.metric_id,
  name: "Support resolution",
  project_id: "project",
  current_version: 1,
};
function list() {
  return (
    <MetricList
      definitions={{ data: { results: [metric], next_cursor: "next-page" } }}
      scope="org:workspace"
      onSelect={vi.fn()}
    />
  );
}
it("makes the page-only scope visible in the compact list search while preserving its current-page filter", async () => {
  mock.get.mockResolvedValue({ data: { results: [], next_cursor: null } });
  mount(list());
  const search = screen.getByRole("textbox", {
    name: "Search metrics on this page",
  });
  expect(search).toHaveAttribute("placeholder", "Search this page");
  fireEvent.change(search, { target: { value: "a metric on another page" } });
  expect(await screen.findByText("No metrics match your search")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: metric.name }),
  ).not.toBeInTheDocument();
});

it.each(["light", "dark"])(
  "displays All units in the closed contributor selector in %s mode and preserves the exact empty group key",
  async (mode) => {
    mock.get.mockResolvedValue({ data: wire.contributors });
    mount(
      <MetricContributors
        scope="org:workspace"
        runId={wire.run.id}
        resultId={wire.run.result_id}
        projectId="project"
        rollup="overall"
        groups={[{ group_key: "" }]}
        expanded
      />,
      mode,
    );
    const select = screen.getByRole("combobox", { name: "Contributor group" });
    expect(select).toHaveTextContent("All units");
    await screen.findByRole("table", { name: "Metric contributors" });
    expect(mock.get).toHaveBeenCalledWith(
      `/runs/${wire.run.id}/contributors`,
      expect.objectContaining({
        params: { rollup: "overall", group_key: "", limit: 20 },
      }),
    );
    fireEvent.mouseDown(select);
    expect(screen.getByRole("option", { name: "All units" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(select).toHaveTextContent("All units");
  },
);

it.each(["light", "dark"])(
  "keeps the Latest value fallback short with the full committed-value explanation available in %s mode",
  async (mode) => {
    mock.get.mockImplementation(async (url) => {
      if (url.endsWith("/runs")) return { data: { results: [wire.run] } };
      if (url.endsWith("/results"))
        return { data: { ...wire.old_page, results: [] } };
      throw new Error(`Unexpected read ${url}`);
    });
    mount(list(), mode);
    const label = await screen.findByText("No result");
    expect(label.closest('[role="row"]')).toHaveTextContent(metric.name);
    fireEvent.mouseOver(label);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "No committed value",
    );
  },
);
