// W5-C-002: exercise the real CSV writer and import parser, not a header stub.
import { describe, expect, it, vi } from "vitest";
import {
  csvRecords,
  importBlockReason,
  parseImport,
} from "src/sections/develop/AddDatasetDrawer/importFile";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {},
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/versionedSource", () => ({
  tableRequestFor: vi.fn(),
}));
vi.mock("src/api/develop/experiment-create", () => ({
  splitsFilter: vi.fn(),
}));

const { datasetFile, datasetRecords } = await import("../datasetExport");

const exportCsv = (names, values, { generatedNames = [] } = {}) => {
  const columns = [...names, ...generatedNames].map((name, index) => ({
    id: `c${index}`,
    name,
    data_type: "text",
    origin_type: index < names.length ? "OTHERS" : "run_prompt",
    role: index < names.length ? "input" : "output",
  }));
  const rows = values.map((cells, index) => ({
    row_id: `internal-${index + 1}`,
    order: index,
    row_meta: { split_ids: ["S1"] },
    ...Object.fromEntries(
      columns.map((column, i) => [column.id, { cell_value: cells[i] }]),
    ),
  }));
  const built = datasetRecords({
    columns,
    rows,
    splitNames: { S1: "Training" },
  });
  const file = datasetFile({
    format: "csv",
    ...built,
    nameParts: ["Reserved headers"],
  });
  return {
    built,
    file,
    header: csvRecords(file.data)[0].cells,
    parsed: parseImport(file.data, "csv"),
  };
};

const expectImportable = ({ parsed, header }, names, values) => {
  expect(parsed.problem).toBeNull();
  expect(parsed.errors).toEqual([]);
  expect(parsed.rejected).toEqual([]);
  expect(parsed.total).toBe(values.length);
  expect(new Set(header.map((name) => name.trim())).size).toBe(header.length);
  expect(header.slice(3)).toEqual(names);
  expect(parsed.rows.map((row) => row.slice(3))).toEqual(values);
  expect(
    importBlockReason({
      name: "Imported download",
      hasFile: true,
      parsed,
      keptColumns: names.length,
    }),
  ).toBeNull();
};

const metadataValues = ["internal-1", "1", "Training"];

describe("W5-C-002 reserved export headers", () => {
  it.each(["row_id", "row", "splits"])(
    "R1: preserves a user %s header and value through CSV re-import",
    (name) => {
      const names = [name, "prompt"];
      const values = [["business-1", "Hello"]];
      const result = exportCsv(names, values);
      expectImportable(result, names, values);
      expect(result.parsed.records[0][name]).toBe("business-1");
      expect(result.parsed.rows[0].slice(0, 3)).toEqual(metadataValues);
      expect(result.header.slice(0, 3)).toEqual(
        ["row_id", "row", "splits"].map((key) =>
          key === name ? `${key} (export)` : key,
        ),
      );
    },
  );

  it("R2: skips every occupied fallback without renaming user columns", () => {
    const names = [
      "row_id",
      "row_id (export)",
      "row_id (export 2)",
      "row",
      "row (export)",
      "row (export 2)",
      "splits",
      "splits (export)",
      "splits (export 2)",
      "prompt",
    ];
    const values = [
      names.map((_, index) => `user-${index}`),
      names.map((_, index) => (index === 0 ? "" : `line ${index}, "quoted"\nnext`)),
    ];
    const result = exportCsv(names, values);
    expectImportable(result, names, values);
    expect(result.header.slice(0, 3)).toEqual([
      "row_id (export 3)",
      "row (export 3)",
      "splits (export 3)",
    ]);
    expect(result.parsed.rows[0].slice(0, 3)).toEqual(metadataValues);
    expect(result.parsed.rows[1].slice(0, 3)).toEqual([
      "internal-2",
      "2",
      "Training",
    ]);
    expect(exportCsv(names, values).file.data).toBe(result.file.data);
  });

  it("R3: accounts for importer trimming without changing emitted user headers", () => {
    const names = [" row_id ", " row_id (export) ", " row ", " splits "];
    const values = [["business-1", "fallback value", "user row", "user split"]];
    const result = exportCsv(names, values);
    expectImportable(result, names, values);
    expect(result.header.slice(0, 3)).toEqual([
      "row_id (export 2)",
      "row (export)",
      "splits (export)",
    ]);
    expect(result.parsed.records[0].row_id).toBe("business-1");
  });

  it("R4: reserves generated column labels before choosing metadata fallbacks", () => {
    const names = ["row_id", "prompt"];
    const generatedNames = ["row_id (export)", "row_id (export 2)"];
    const values = [["business-1", "Hello", "output one", "output two"]];
    const result = exportCsv(names, values, { generatedNames });
    expectImportable(result, [...names, ...generatedNames], values);
    expect(result.header[0]).toBe("row_id (export 3)");
  });

  it("R5: can export an imported download again without another collision", () => {
    const first = exportCsv(["row_id", "prompt"], [["business-1", "Hello"]]);
    const second = exportCsv(first.parsed.columns, first.parsed.rows);
    expectImportable(second, first.parsed.columns, first.parsed.rows);
    expect(second.parsed.records[0].row_id).toBe("business-1");
    expect(second.header.slice(0, 3)).toEqual([
      "row_id (export 2)",
      "row (export)",
      "splits (export)",
    ]);
  });

  it("C1: leaves CSV bytes unchanged when no metadata label collides", () => {
    const result = exportCsv(["prompt"], [["Hello"]]);
    expectImportable(result, ["prompt"], [["Hello"]]);
    expect(result.file.data).toBe(
      "row_id,row,splits,prompt\r\ninternal-1,1,Training,Hello\r\n",
    );
  });

  it("C2: changes only flat labels, not JSON records or row value keys", () => {
    const { built } = exportCsv(["row_id", "row", "splits"], [
      ["business-1", "user row", "user split"],
    ]);
    const record = {
      row_id: "internal-1",
      row: 1,
      splits: ["Training"],
      input: { row_id: "business-1", row: "user row", splits: "user split" },
    };
    expect(built.records).toEqual([record]);
    expect(built.flatRows[0]).toEqual({
      row_id: "internal-1",
      row: 1,
      splits: "Training",
      "u:c0": "business-1",
      "u:c1": "user row",
      "u:c2": "user split",
    });
    expect(built.typedRows).toEqual(built.flatRows);
    expect(built.flat.map((column) => column.key)).toEqual([
      "row_id", "row", "splits", "u:c0", "u:c1", "u:c2",
    ]);
    const json = datasetFile({ format: "json", ...built, nameParts: ["x"] });
    expect(JSON.parse(json.data).rows).toEqual([record]);
  });

  it("C3: still rejects genuinely duplicated user headers", () => {
    const parsed = parseImport("prompt,prompt\r\nHello,World\r\n", "csv");
    expect(parsed.problem).toContain('Column "prompt" appears twice');
    expect(
      importBlockReason({
        name: "Duplicate input",
        hasFile: true,
        parsed,
        keptColumns: 2,
      }),
    ).toBe(parsed.problem);
  });
});
