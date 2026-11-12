// W3-6 B3: the results export keeps exactly the view (R3 order, paged),
// names the filter and sort in its header, and lays rows out wide or long.
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    develop: {
      datasetRuns: { results: (id) => `/model-hub/datasets/${id}/results/` },
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
    },
  },
}));

const {
  everyRowView,
  fetchResultsExport,
  resultsFile,
  resultsMeta,
  resultsRecords,
  resultsViewOf,
  viewIsNarrowed,
  viewParts,
} = await import("../resultsExport");

const RUN_A = {
  run_id: "RA",
  label: "#2A",
  name: "A",
  dataset_version: { number: 5 },
  status: "Completed",
};
const RUN_B = {
  run_id: "RB",
  label: "#2B",
  name: "B",
  dataset_version: { number: 5 },
  status: "Completed",
};
const SUMMARY = {
  baseline: "RA",
  runs: [RUN_A, RUN_B],
  metrics: [
    {
      key: "eval:correctness",
      name: "Correctness",
      kind: "eval",
      versions: { RA: "v2", RB: "v2" },
    },
    { key: "human:quality", name: "Quality", kind: "human" },
  ],
};
const res = (id, out, extra = {}) => ({
  status: "completed",
  output: out,
  trials: 1,
  latency_ms: 1000,
  cost: 0.01,
  trace_ids: [`T_${id}`],
  scores: {
    "eval:correctness": { value: 0, display: "Fail", reason: `why ${id}` },
  },
  ...extra,
});
const ROWS = ["R9", "R2", "R5"].map((id, i) => ({
  example_id: id,
  row_order: 10 - i,
  splits: ["smoke"],
  inputs: { q: `question ${id}` },
  expected: { value: { a: `answer ${id}` } },
  metadata: { tier: "pro" },
  results: {
    RA: res(`${id}A`, `out A ${id}`),
    RB: res(`${id}B`, `out B ${id}`, {
      human: { "human:quality": { value: 4 } },
    }),
  },
}));
const VIEW = {
  runIds: ["RA", "RB"],
  baselineId: "RA",
  pills: [{ type: "fails", metric: "eval:correctness", run: "any_run" }],
  show: "all",
  search: "",
  sort: "latency:RB",
  agg: "avg",
};

beforeEach(() => {
  mocks.get.mockReset();
});

describe("W3-6 B3 · results export", () => {
  it("reads the page's view from the URL like the grid (show needs two runs; default sort)", () => {
    const search = `?filters=${encodeURIComponent(JSON.stringify(VIEW.pills))}&show=worse`;
    expect(
      resultsViewOf({ search, runIds: ["RA", "RB"], baselineId: "RA" }),
    ).toMatchObject({
      pills: VIEW.pills,
      show: "regressed",
      sort: "regressed",
    });
    expect(
      resultsViewOf({ search, runIds: ["RA"], baselineId: "RA" }),
    ).toMatchObject({ show: "all", sort: "order" });
    expect(
      resultsViewOf({ search: "?sort=cost:RA", runIds: ["RA"], view: VIEW }),
    ).toBe(VIEW);
    expect(viewIsNarrowed(VIEW)).toBe(true);
    expect(viewIsNarrowed(everyRowView(VIEW))).toBe(false);
  });

  it("names the filter and the sort", () => {
    expect(viewParts(VIEW, SUMMARY)).toEqual([
      "Fails Correctness · any run",
      "sorted by Slowest · #2B",
    ]);
    expect(
      viewParts(
        { ...VIEW, pills: [], show: "regressed", sort: "regressed" },
        SUMMARY,
      ),
    ).toEqual(["worse than baseline"]);
    expect(
      viewParts(
        { ...VIEW, pills: [], show: "all", sort: "regressed" },
        SUMMARY,
      ),
    ).toEqual(["worse than baseline first"]);
  });

  it("pages R3 with the grid's params and keeps the view order, then reads each row in full", async () => {
    mocks.get.mockImplementation(async (url, { params }) => {
      if (params.example_id) {
        const row = ROWS.find((r) => r.example_id === params.example_id);
        return { data: { result: { rows: [{ ...row, full: true }] } } };
      }
      const page = params.page;
      return {
        data: {
          result: {
            total: 3,
            rows: ROWS.slice(page * 2, page * 2 + 2),
            ...(page === 0 ? { summary: SUMMARY } : {}),
          },
        },
      };
    });
    const got = await fetchResultsExport("DS", VIEW, { pageSize: 2 });
    expect(got.rows.map((r) => r.example_id)).toEqual(["R9", "R2", "R5"]);
    expect(got.rows.every((r) => r.full)).toBe(true);
    const pages = mocks.get.mock.calls.filter(([, o]) => !o.params.example_id);
    expect(pages.map(([, o]) => o.params.page)).toEqual([0, 1]);
    expect(pages[0][1].params).toMatchObject({
      runs: "RA,RB",
      baseline: "RA",
      sort: "latency:RB",
      filters: JSON.stringify(VIEW.pills),
    });
  });

  it("one line per row (wide): runs keyed by label with output, eval + reason, trials, latency, cost, trace ids, human labels", () => {
    const { records, flat, flatRows } = resultsRecords({
      rows: ROWS,
      summary: SUMMARY,
    });
    expect(records.map((r) => r.row_id)).toEqual(["R9", "R2", "R5"]);
    expect(records[0]).toMatchObject({
      dataset_version: 5,
      baseline_run: "#2A",
      inputs: { q: "question R9" },
      expected: { a: "answer R9" },
    });
    expect(records[0].runs["#2B"]).toMatchObject({
      output: "out B R9",
      trials: 1,
      latency_ms: 1000,
      cost: 0.01,
      trace_ids: ["T_R9B"],
      evals: { Correctness: { value: 0, display: "Fail", reason: "why R9B" } },
      human_labels: { Quality: 4 },
    });
    const labels = flat.map((c) => c.label);
    expect(labels).toEqual(
      expect.arrayContaining([
        "dataset_version",
        "baseline_run",
        "#2A output",
        "#2B Correctness",
        "#2B Correctness reason",
        "#2B Quality",
        "#2A trace_ids",
      ]),
    );
    expect(flatRows[1]["#2B e:eval:correctness"]).toBe("Fail");
  });

  it("one line per row and run (long) has a run column", () => {
    const { records, flat, flatRows } = resultsRecords({
      rows: ROWS,
      summary: SUMMARY,
      layout: "run",
    });
    expect(records).toHaveLength(6);
    expect(records.map((r) => `${r.row_id}${r.run}`)).toEqual([
      "R9#2A",
      "R9#2B",
      "R2#2A",
      "R2#2B",
      "R5#2A",
      "R5#2B",
    ]);
    expect(flat.map((c) => c.label).slice(0, 6)).toEqual([
      "row_id",
      "row",
      "splits",
      "dataset_version",
      "baseline_run",
      "run",
    ]);
    expect(flatRows[0].run).toBe("#2A");
  });

  it("the header names dataset, versions, runs, baseline, eval versions, filters and sort; CSV context line only on request", () => {
    const meta = resultsMeta({
      dataset: "GS",
      view: VIEW,
      summary: SUMMARY,
      exportedAt: "2026-10-22T00:00:00.000Z",
      scope: "view",
    });
    expect(meta).toMatchObject({
      dataset: "GS",
      dataset_version: 5,
      baseline: "#2A",
      eval_versions: { Correctness: { "#2A": "v2", "#2B": "v2" } },
      filters: ["Fails Correctness · any run"],
      sort: "sorted by Slowest · #2B",
      exported_at: "2026-10-22T00:00:00.000Z",
    });
    expect(meta.runs.map((r) => r.run)).toEqual(["#2A", "#2B"]);
    const all = resultsMeta({
      dataset: "GS",
      view: VIEW,
      summary: SUMMARY,
      exportedAt: "x",
      scope: "all",
    });
    expect(all.filters).toEqual([]);
    const built = resultsRecords({ rows: ROWS, summary: SUMMARY });
    const plain = resultsFile({
      format: "csv",
      ...built,
      meta,
      nameParts: ["GS"],
    });
    expect(plain.data.startsWith("row_id,")).toBe(true);
    const withLine = resultsFile({
      format: "csv",
      ...built,
      meta,
      contextLine: true,
      nameParts: ["GS"],
    });
    expect(withLine.data.split("\r\n")[0]).toMatch(
      /^# GS · dataset v5 · runs #2A, #2B · baseline #2A · Fails Correctness/,
    );
    const json = JSON.parse(
      resultsFile({ format: "json", ...built, meta, nameParts: ["GS"] }).data,
    );
    expect(json.meta.filters).toEqual(["Fails Correctness · any run"]);
    expect(json.rows).toHaveLength(3);
  });
});
