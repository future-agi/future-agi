// Download… of "This view" with `Hide rows with no output` on asked the
// results endpoint for the switch's expression, which it refuses (400
// "filters: Unsupported filter type in this checkpoint."). The pill never
// reaches the server now and the file leaves out the rows the grid hides.
import { beforeEach, describe, expect, it, vi } from "vitest";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    develop: {
      datasetRuns: { results: (id) => `/model-hub/datasets/${id}/results/` },
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
    },
  },
}));

const { fetchResultsExport } = await import("../resultsExport");
const { withHideEmpty } = await import("src/sections/common/filters/hideEmpty");

const ROWS = [
  {
    example_id: "e1",
    results: { RA: { output: "yes" }, RB: { output: null } },
  },
  { example_id: "e2", results: { RA: { output: null }, RB: { output: "" } } },
  { example_id: "e3", results: { RA: { output: null }, RB: { output: "no" } } },
];
const SERVER_TYPES = new Set([
  "rows",
  "meta",
  "split",
  "error",
  "flaky",
  "score",
  "fails",
  "latency",
  "cost",
]);
const server = (_url, { params }) => {
  const pills = params.filters ? JSON.parse(params.filters) : [];
  if (pills.some((p) => !SERVER_TYPES.has(p.type)))
    return Promise.reject({
      statusCode: 400,
      result: "filters: Unsupported filter type in this checkpoint.",
    });
  const rows = params.full
    ? ROWS.filter((r) => r.example_id === params.example_id)
    : ROWS;
  return Promise.resolve({
    data: { status: true, result: { total: ROWS.length, rows, summary: {} } },
  });
};

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(server);
});

describe("Download… with Hide rows with no output", () => {
  it("writes only the rows the grid shows", async () => {
    const got = await fetchResultsExport("ds-1", {
      runIds: ["RA", "RB"],
      baselineId: "RA",
      pills: withHideEmpty([], true),
      show: "all",
      sort: "order",
    });
    expect(got.rows.map((r) => r.example_id)).toEqual(["e1", "e3"]);
    expect(got.total).toBe(2);
    for (const [, { params }] of get.mock.calls)
      expect(params.filters ?? "").not.toContain("expression");
  });

  it("writes every row with the switch off", async () => {
    const got = await fetchResultsExport("ds-1", {
      runIds: ["RA", "RB"],
      baselineId: "RA",
      pills: [],
      show: "all",
      sort: "order",
    });
    expect(got.rows.map((r) => r.example_id)).toEqual(["e1", "e2", "e3"]);
    expect(got.total).toBe(3);
  });
});
