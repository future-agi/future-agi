import { describe, it, expect } from "vitest";
import {
  buildImportColumns,
  buildVariableData,
  getMappingValue,
} from "../utils";

const DATASET_DETAIL = {
  column_config: [
    { id: "col_q", name: "question", data_type: "text" },
    { id: "col_a", name: "answer", data_type: "text" },
    { id: "col_img", name: "image", data_type: "image" },
  ],
  table: [
    {
      row_id: "r1",
      col_q: { cell_value: "What is 2+2?" },
      col_a: { cell_value: "4" },
    },
    {
      row_id: "r2",
      col_q: { cell_value: "Capital of France?" },
      col_a: { cell_value: "Paris" },
    },
  ],
};

describe("ImportDataset utils — get-dataset-table (snake_case)", () => {
  describe("buildImportColumns", () => {
    it("returns only text columns mapped to {headerName, field}", () => {
      expect(buildImportColumns(DATASET_DETAIL)).toEqual([
        { headerName: "question", field: "col_q" },
        { headerName: "answer", field: "col_a" },
      ]);
    });

    it("reads column_config/data_type, not columnConfig/dataType (regression guard)", () => {
      const camelShaped = {
        columnConfig: [{ id: "c", name: "n", dataType: "text" }],
      };
      expect(buildImportColumns(camelShaped)).toEqual([]);
      expect(buildImportColumns(DATASET_DETAIL).length).toBeGreaterThan(0);
    });

    it("returns [] when detail or column_config is missing", () => {
      expect(buildImportColumns(undefined)).toEqual([]);
      expect(buildImportColumns({})).toEqual([]);
    });
  });

  describe("buildVariableData", () => {
    it("imports cell_value for mapped columns", () => {
      const mapping = { ONE: "col_q", TWO: "col_a" };
      expect(
        buildVariableData(mapping, DATASET_DETAIL, ["ONE", "TWO", "THREE"]),
      ).toEqual({
        ONE: ["What is 2+2?", "Capital of France?"],
        TWO: ["4", "Paris"],
      });
    });

    it("reads cell_value, not cellValue (regression guard — silent data loss)", () => {
      const result = buildVariableData({ ONE: "col_q" }, DATASET_DETAIL, [
        "ONE",
      ]);
      // Would be ["", ""] if it read cell.cellValue.
      expect(result.ONE).toEqual(["What is 2+2?", "Capital of France?"]);
      expect(result.ONE.every((v) => v !== "")).toBe(true);
    });

    it("falls back to '' for a missing cell", () => {
      const detail = { table: [{ row_id: "r1" }] };
      expect(buildVariableData({ ONE: "col_q" }, detail, ["ONE"])).toEqual({
        ONE: [""],
      });
    });

    it("skips unmapped variables", () => {
      expect(buildVariableData({}, DATASET_DETAIL, ["ONE"])).toEqual({});
    });
  });

  describe("getMappingValue", () => {
    it("resolves nested paths", () => {
      expect(getMappingValue({ a: { b: "x" } }, "a.b")).toBe("x");
    });

    it("returns null for missing path or empty input", () => {
      expect(getMappingValue({ a: 1 }, "a.b")).toBeNull();
      expect(getMappingValue(null, "a")).toBeNull();
      expect(getMappingValue({ a: 1 }, "")).toBeNull();
    });
  });
});
