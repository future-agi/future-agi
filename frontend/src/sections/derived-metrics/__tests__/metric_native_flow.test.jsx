import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "src/utils/test-utils";
import MetricList from "../MetricList";
import { initialForm } from "../metricForm";
import { metricDashboardDescription } from "../metric_dashboard_description";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: {
    derivedMetrics: {
      runs: (id) => `/metrics/${id}/runs`,
      results: (id) => `/runs/${id}/results`,
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
  name: "Average latency for all support conversations with a deliberately long saved name",
  project_id: "project",
  current_version: 2,
  definition: { ...initialForm(), version: 2 },
};
let client;
let metrics;
let runs;
let resultPage;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MetricList
        definitions={{ data: { results: metrics, next_cursor: null } }}
        scope="org:ws"
        onSelect={vi.fn()}
      />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  metrics = [structuredClone(metric)];
  runs = [structuredClone(wire.run)];
  resultPage = structuredClone(wire.old_page);
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: runs, next_cursor: null } };
    if (url === `/runs/${wire.run.id}/results`) return { data: resultPage };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

it.each([0, null])(
  "never coerces latest %s to a different value",
  async (value) => {
    resultPage.results[0].value = value;
    mount();
    expect(
      await screen.findByText(value === null ? "No value" : "0"),
    ).toBeInTheDocument();
  },
);

it("does not label the data window as the execution time when run time is unavailable", async () => {
  delete runs[0].created_at;
  delete runs[0].as_of;
  mount();
  expect(
    await screen.findByText("Time unavailable · complete"),
  ).toBeInTheDocument();
});

it("does not show an unverified latest result, and offers retry", async () => {
  resultPage.result_id = "00000000-0000-0000-0000-000000000099";
  mount();
  expect(await screen.findByText("Unavailable")).toBeInTheDocument();
  expect(screen.queryByText("0.5")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Retry latest run" }),
  ).toBeInTheDocument();
});

it("normalizes only older generated UUID descriptions without mutating ordinary descriptions", () => {
  expect(
    metricDashboardDescription(
      `Frozen metric run ${wire.run.id}. A point represents the complete run window.`,
    ),
  ).toBe("Saved metric result. A point represents the complete run window.");
  expect(metricDashboardDescription(`Incident ${wire.run.id}`)).toBe(
    `Incident ${wire.run.id}`,
  );
});
