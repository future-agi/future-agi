import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
import MetricList from "../MetricList";
import MetricDefinitionEditor from "../metric_definition_editor";
import SelectedRunSummary from "../SelectedRunSummary";
import { initialForm } from "../metricForm";
import wire from "./fixtures/committedResults.json";

vi.mock("../api", () => ({
  useMetricKpi: () => ({ readState: "ready", data: { current: null } }),
}));
const mock = vi.hoisted(() => ({ get: vi.fn() }));
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
let client;
let names;
function mountElement(element, mode = "light") {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
    { theme: createTheme({ palette: { mode } }) },
  );
}
function mount(mode) {
  return mountElement(
    <MetricList
      definitions={{ data: { results: names, next_cursor: null } }}
      scope={{ organizationId: "org", workspaceId: "ws" }}
      onSelect={vi.fn()}
    />,
    mode,
  );
}
function mountEditor(mode) {
  function Editor() {
    const [form, setForm] = useState(initialForm);
    return (
      <MetricDefinitionEditor
        form={form}
        projects={{
          data: { rows: [{ id: "project", name: "Support" }], totalPages: 1 },
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
  return mountElement(<Editor />, mode);
}
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const choose = async (label, option) => {
  fireEvent.mouseDown(
    screen.getByLabelText(label, { selector: '[role="combobox"]' }),
  );
  fireEvent.click(await screen.findByRole("option", { name: option }));
  await waitFor(() =>
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
  );
};
beforeEach(() => {
  names = [metric];
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: [run], next_cursor: null } };
    if (url === `/runs/${run.id}/results`)
      return { data: { ...wire.old_page, results: [result] } };
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
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
      // Shared Button sizing owns density; the text span owns ellipsis.
      expect(row).toHaveClass("MuiButton-sizeSmall");
      expect(row).toHaveStyle({
        whiteSpace: "nowrap",
        minWidth: "0",
        width: "100%",
        overflow: "hidden",
      });
      expect(within(row).getByText(long)).toHaveStyle({
        minWidth: "0",
        overflow: "hidden",
        textOverflow: "ellipsis",
      });
      expect(row).toHaveAttribute("title", long);
      expect(screen.getByRole("cell", { name: "v2" })).toBeInTheDocument();
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

  it.each(["light", "dark"])(
    "bounds Data, Calculation and Review widths and keeps a full-height surface in %s",
    async (mode) => {
      mountEditor(mode);
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
});
