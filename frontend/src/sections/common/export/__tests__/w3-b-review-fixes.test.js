// W3-6 review fixes: M1 the results "This view" takes the grid's search
// from the grid's own read; M2 a compare across dataset versions names each
// run's version (no empty shared column); M3 Parquet and JSON type float /
// integer / boolean columns by their data_type while the CSV keeps the
// cell text byte for byte.
import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    develop: {
      datasetRuns: { results: (id) => `/model-hub/datasets/${id}/results/` },
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
    },
  },
}));

const { resultsFile, resultsRecords, resultsViewOf, viewFromGridReads } =
  await import("../resultsExport");
const { datasetFile, datasetRecords, typedColumnsOf } = await import(
  "../datasetExport"
);
const { runResultsParams, RESULTS_PAGE_SIZE } = await import(
  "src/api/develop/run-results"
);
const { parquetTypeOf } = await import("../writers");

const PILLS = [{ type: "fails", metric: "eval:correctness", run: "any_run" }];
const urlView = () =>
  resultsViewOf({
    search: `?filters=${encodeURIComponent(JSON.stringify(PILLS))}&sort=${encodeURIComponent("latency:RB")}`,
    runIds: ["RA", "RB"],
    baselineId: "RA",
  });
// The params a grid read carries (useRunResults: view params + page).
const gridRead = (view) => ({
  ...runResultsParams(view),
  page: 0,
  page_size: RESULTS_PAGE_SIZE,
});

describe("M1 · This view = the grid's view, search included", () => {
  it("a URL view is marked as such and takes the grid's search from its live read", () => {
    const v = urlView();
    expect(v.source).toBe("url");
    expect(v.search).toBe("");
    const got = viewFromGridReads(v, [gridRead({ ...v, search: "refund" })]);
    expect(got).toMatchObject({
      runIds: ["RA", "RB"],
      pills: PILLS,
      sort: "latency:RB",
      search: "refund",
    });
    expect(runResultsParams(got)).toEqual(
      runResultsParams({ ...v, search: "refund" }),
    );
  });

  it("no search in the grid: the URL view (search empty)", () => {
    const v = urlView();
    expect(viewFromGridReads(v, [gridRead(v)]).search).toBe("");
  });

  it("no live grid read of these runs (no grid on the page): the URL view as is", () => {
    const v = urlView();
    expect(viewFromGridReads(v, [])).toBe(v);
    const other = gridRead({ runIds: ["RX"], search: "refund" });
    expect(viewFromGridReads(v, [other])).toBe(v);
  });

  it("the grid's read differs from the URL, or two reads disagree: null (no 'This view')", () => {
    const v = urlView();
    expect(
      viewFromGridReads(v, [gridRead({ ...v, sort: "cost:RA", search: "x" })]),
    ).toBeNull();
    expect(
      viewFromGridReads(v, [
        gridRead({ ...v, search: "refund" }),
        gridRead({ ...v, search: "late" }),
      ]),
    ).toBeNull();
  });

  it("a view the page passed itself is kept", () => {
    const passed = { ...urlView(), source: undefined, search: "mine" };
    expect(
      viewFromGridReads(passed, [gridRead({ ...passed, search: "x" })]),
    ).toBe(passed);
  });
});

const run = (id, label, version) => ({
  run_id: id,
  label,
  dataset_version: { number: version },
  status: "Completed",
});
const SUMMARY = (versions) => ({
  baseline: "R1",
  runs: [run("R1", "#1A", versions[0]), run("R4", "#4A", versions[1])],
  metrics: [{ key: "eval:correctness", name: "Correctness", kind: "eval" }],
});
const ROWS = ["E1", "E2"].map((id, i) => ({
  example_id: id,
  row_order: i + 1,
  splits: [],
  inputs: { q: `q ${id}` },
  expected: { value: { a: `a ${id}` } },
  metadata: {},
  results: {
    R1: { status: "completed", output: `o1 ${id}`, trace_ids: [`T1${id}`] },
    R4: { status: "completed", output: `o4 ${id}`, trace_ids: [`T4${id}`] },
  },
}));
const parseCsv = (text) =>
  text
    .trimEnd()
    .split("\r\n")
    .map((l) => l.split(","));

describe("M2 · wide results across dataset versions", () => {
  it("runs on different versions: each run's columns start with its own dataset_version; no empty shared column", () => {
    const built = resultsRecords({ rows: ROWS, summary: SUMMARY([5, 6]) });
    const labels = built.flat.map((c) => c.label);
    expect(labels).not.toContain("dataset_version");
    expect(labels).toEqual(
      expect.arrayContaining(["#1A dataset_version", "#4A dataset_version"]),
    );
    expect(labels.indexOf("#1A dataset_version")).toBe(
      labels.indexOf("#1A output") - 1,
    );
    const csv = parseCsv(
      resultsFile({ format: "csv", ...built, meta: {}, nameParts: ["GS"] })
        .data,
    );
    const [header, ...body] = csv;
    const i1 = header.indexOf("#1A dataset_version");
    const i4 = header.indexOf("#4A dataset_version");
    expect(body.map((r) => [r[i1], r[i4]])).toEqual([
      ["5", "6"],
      ["5", "6"],
    ]);
    expect(built.records[0].dataset_version).toEqual({ "#1A": 5, "#4A": 6 });
    expect(built.records[0].runs["#4A"].dataset_version).toBe(6);
  });

  it("runs on one version: one shared dataset_version column, filled on every row", () => {
    const built = resultsRecords({ rows: ROWS, summary: SUMMARY([5, 5]) });
    const labels = built.flat.map((c) => c.label);
    expect(labels).toContain("dataset_version");
    expect(labels.some((l) => / dataset_version$/.test(l))).toBe(false);
    expect(built.flatRows.map((r) => r.dataset_version)).toEqual([5, 5]);
    expect(built.records.map((r) => r.dataset_version)).toEqual([5, 5]);
  });
});

const col = (id, name, dataType, extra = {}) => ({
  id,
  name,
  data_type: dataType,
  origin_type: "OTHERS",
  role: "metadata",
  ...extra,
});
const COLUMNS = [
  col("q", "question", "text", { role: "input" }),
  col("n", "attempts", "integer"),
  col("s", "score", "float"),
  col("b", "vip", "boolean"),
  col("m", "mixed", "float"),
  col("g", "Groundedness", "float", {
    origin_type: "evaluation",
    role: "eval",
    source_id: "G",
  }),
  col("c", "Correctness", "boolean", {
    origin_type: "evaluation",
    role: "eval",
    source_id: "C",
  }),
];
const cell = (v) => ({ cell_value: v });
const TROWS = [
  {
    row_id: "r1",
    order: 0,
    q: cell("hi"),
    n: cell("3"),
    s: cell("0.90"),
    b: cell("true"),
    m: cell("0.5"),
    g: cell("1"),
    c: cell("Passed"),
  },
  {
    row_id: "r2",
    order: 1,
    q: cell("yo"),
    n: cell(""),
    s: cell("1e-3"),
    b: cell("False"),
    m: cell("n/a"),
    g: cell("0.25"),
    c: cell("Failed"),
  },
];

describe("M3 · typed Parquet and JSON, CSV unchanged", () => {
  it("types a float / integer / boolean column only when every value parses", () => {
    const typed = typedColumnsOf(COLUMNS, TROWS);
    expect(Object.keys(typed).sort()).toEqual(["b", "c", "g", "n", "s"]);
    expect(typed.m).toBeUndefined();
  });

  it("JSON records and the Parquet rows hold numbers and booleans; the CSV holds the cell text", () => {
    const built = datasetRecords({ columns: COLUMNS, rows: TROWS });
    expect(built.records[0].metadata).toEqual({
      attempts: 3,
      score: 0.9,
      vip: true,
      mixed: "0.5",
    });
    expect(built.records[1].metadata).toEqual({
      attempts: null,
      score: 0.001,
      vip: false,
      mixed: "n/a",
    });
    expect(built.records[0].generated.Groundedness.value).toBe(1);
    expect(built.records[0].generated.Correctness.value).toBe(true);
    expect(built.records[1].generated.Correctness.value).toBe(false);
    const byLabel = Object.fromEntries(built.flat.map((c) => [c.label, c]));
    expect(byLabel.attempts.type).toBe("INT64");
    expect(byLabel.score.type).toBe("DOUBLE");
    expect(byLabel.vip.type).toBe("BOOLEAN");
    expect(byLabel.Groundedness.type).toBe("DOUBLE");
    expect(byLabel.Correctness.type).toBe("BOOLEAN");
    expect(byLabel.mixed.type).toBeUndefined();
    expect(byLabel["Groundedness reason"].type).toBeUndefined();
    // Parquet column types from the typed rows: a float column of whole
    // numbers stays DOUBLE.
    const typeOf = (label) =>
      parquetTypeOf(
        built.typedRows.map((r) => r[byLabel[label].key]),
        byLabel[label].type,
      );
    expect(typeOf("Groundedness")).toBe("DOUBLE");
    expect(typeOf("attempts")).toBe("INT64");
    expect(typeOf("Correctness")).toBe("BOOLEAN");
    expect(typeOf("mixed")).toBe("BYTE_ARRAY");
    expect(typeOf("question")).toBe("BYTE_ARRAY");
    // CSV: the cell text byte for byte (0.90 stays 0.90, Passed stays Passed).
    const csv = datasetFile({ format: "csv", ...built, nameParts: ["x"] }).data;
    expect(csv.split("\r\n")[1]).toBe("r1,1,,hi,3,0.90,true,0.5,1,,Passed,");
    expect(csv.split("\r\n")[2]).toBe("r2,2,,yo,,1e-3,False,n/a,0.25,,Failed,");
  });

  it("the Parquet file is written from the typed rows", () => {
    const built = datasetRecords({ columns: COLUMNS, rows: TROWS });
    const pq = datasetFile({ format: "parquet", ...built, nameParts: ["x"] });
    expect(pq.data).toBeInstanceOf(Uint8Array);
    // The PLAIN DOUBLE page of Groundedness holds 1 and 0.25 as float64.
    const bytes = new Uint8Array(16);
    const dv = new DataView(bytes.buffer);
    dv.setFloat64(0, 1, true);
    dv.setFloat64(8, 0.25, true);
    const hay = Array.from(pq.data).join(",");
    expect(hay.includes(Array.from(bytes).join(","))).toBe(true);
  });
});
