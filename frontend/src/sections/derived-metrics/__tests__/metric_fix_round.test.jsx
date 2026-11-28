import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import MetricList from "../MetricList";
import MetricDefinitionEditor from "../metric_definition_editor";
import SelectedRunSummary from "../SelectedRunSummary";
import wire from "./fixtures/committedResults.json";
import { initialForm } from "../metricForm";

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
  current_version: 1,
  definition: wire.run.definition,
};
let client;
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
function mount(element) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
function listElement(projects) {
  return (
    <MetricList
      definitions={{
        data: {
          results: [
            metric,
            {
              ...metric,
              id: "00000000-0000-0000-0000-000000000002",
              name: "Other metric",
              project_id: "other-project",
            },
          ],
        },
      }}
      scope={{ organizationId: "org", workspaceId: "ws" }}
      projects={projects}
      onSelect={vi.fn()}
      onBefore={vi.fn()}
    />
  );
}
beforeEach(() => {
  mock.projects.mockReset().mockResolvedValue({
    rows: [{ id: "project", name: "Support" }],
    totalPages: 1,
  });
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url.endsWith("/runs"))
      return {
        data: {
          results: url === `/metrics/${metric.id}/runs` ? [wire.run] : [],
          next_cursor: null,
        },
      };
    if (url.endsWith("/results")) return { data: wire.old_page };
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

it("M4 resolves all visible projects independently of the editor search and page", async () => {
  mock.projects.mockImplementation(async ({ params }) => ({
    rows:
      params.page_number === 0
        ? [{ id: "project", name: "Support" }]
        : [{ id: "other-project", name: "International" }],
    totalPages: 2,
  }));
  const view = mount(
    listElement({
      data: { rows: [{ id: "unrelated", name: "Editor search" }] },
    }),
  );
  expect(await screen.findByText("Support")).toBeInTheDocument();
  expect(await screen.findByText("International")).toBeInTheDocument();
  expect(mock.projects).toHaveBeenCalledWith(
    expect.objectContaining({
      params: expect.objectContaining({ page_number: 1, page_size: 50 }),
    }),
  );
  view.rerender(
    <QueryClientProvider client={client}>
      {listElement({
        data: { rows: [{ id: "project", name: "Wrong editor-only name" }] },
      })}
    </QueryClientProvider>,
  );
  expect(screen.getByText("Support")).toBeInTheDocument();
  expect(screen.queryByText("Wrong editor-only name")).not.toBeInTheDocument();
});

it("M4 shows honest loading and neutral fallback rather than borrowing editor names", async () => {
  const pending = deferred();
  mock.projects.mockReturnValue(pending.promise);
  mount(
    listElement({
      data: { rows: [{ id: "project", name: "Editor-only name" }] },
    }),
  );
  expect(await screen.findAllByText("Loading project…")).toHaveLength(2);
  expect(screen.queryByText("Editor-only name")).not.toBeInTheDocument();
  await act(async () => pending.resolve({ rows: [], totalPages: 1 }));
  expect(await screen.findAllByText("Project name unavailable")).toHaveLength(
    2,
  );
});

it.each([
  ["2026-10-27T10:00:00.100000Z", "2026-10-27T10:00:00.900000Z"],
  ["2026-10-27T10:00:00.123456Z", "2026-10-27T10:00:01.654321Z"],
])("M5 discloses exact saved boundaries %s to %s", (start, end) => {
  mount(
    <SelectedRunSummary
      run={{ ...wire.run, window_start: start, window_end: end }}
      result={wire.old_page.results[0]}
      detailsOpen
    />,
  );
  const summary = screen.getByRole("region", { name: "Selected saved run" });
  expect(summary).toHaveTextContent(
    `Exact saved boundaries: ${start} to ${end}`,
  );
  expect(summary).toHaveTextContent("Window (UTC):");
});

it.each([false, true])(
  "M6 keeps final-step Validate and Save compact (editing=%s)",
  (editing) => {
    mount(
      <MetricDefinitionEditor
        form={{ ...initialForm(), name: "Latency", project_id: "project" }}
        metric={editing ? metric : null}
        projects={{ data: { rows: [{ id: "project", name: "Support" }] } }}
        onChange={vi.fn()}
        onReplace={vi.fn()}
        onValidate={vi.fn()}
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    click("Continue");
    click("Continue");
    expect(
      screen.getByRole("button", { name: "Validate formula" }),
    ).toHaveClass("MuiButton-sizeSmall");
    expect(
      screen.getByRole("button", {
        name: editing ? "Save new version" : "Save metric",
      }),
    ).toHaveClass("MuiButton-sizeSmall");
    expect(screen.getByRole("button", { name: "Back" })).toHaveClass(
      "MuiButton-sizeSmall",
    );
  },
);
