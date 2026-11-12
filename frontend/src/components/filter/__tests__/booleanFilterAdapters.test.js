import { describe, expect, it } from "vitest";
import { panelFilterToStore } from "../storeFilterAdapter";
import { fromPanelValue as fromDatasetPanelValue } from "../datasetColumnAdapter";
import {
  buildFilterLeaves,
  fromPanelValue as fromComplexPanelValue,
  toPanelValue as toComplexPanelValue,
} from "../complexFilterAdapter";

const booleanCases = [
  { label: "Basic string true", value: "true", expected: true },
  { label: "Basic string false", value: "false", expected: false },
  { label: "Basic boolean true", value: true, expected: true },
  { label: "Basic boolean false", value: false, expected: false },
  { label: "Query string true", value: ["true"], expected: true },
  { label: "Query string false", value: ["false"], expected: false },
  { label: "Query boolean true", value: [true], expected: true },
  { label: "Query boolean false", value: [false], expected: false },
];

const panelRow = (value) => ({
  field: "approved",
  fieldType: "boolean",
  operator: "equals",
  value,
});

const complexDefinitions = [
  {
    label: "fixed boolean column",
    definition: {
      propertyName: "Approved",
      propertyId: "approved",
      filterType: { type: "boolean" },
    },
  },
  {
    label: "type-selectable column",
    definition: {
      propertyName: "Approved",
      propertyId: "approved",
      filterType: { type: "text" },
      allowTypeChange: true,
    },
  },
];

describe("W10-FILTERS-L01 boolean filter adapter parity", () => {
  describe("dataset store", () => {
    it.each(booleanCases)("preserves $label", ({ value, expected }) => {
      const filter = panelFilterToStore(panelRow(value));

      expect(filter.columnId).toBe("approved");
      expect(filter.filterConfig).toEqual({
        filterType: "boolean",
        filterOp: "equals",
        filterValue: expected,
      });
    });
  });

  describe("evaluation-log dataset columns", () => {
    it.each(booleanCases)("preserves $label", ({ value, expected }) => {
      const filters = fromDatasetPanelValue([panelRow(value)]);

      expect(filters).toHaveLength(1);
      expect(filters[0].columnId).toBe("approved");
      expect(filters[0].filterConfig).toEqual({
        filterType: "boolean",
        filterOp: "equals",
        filterValue: String(expected),
      });
    });
  });

  describe.each(complexDefinitions)(
    "ComplexFilter $label",
    ({ definition }) => {
      const leaves = buildFilterLeaves([definition]);

      it.each(booleanCases)("preserves $label", ({ value, expected }) => {
        const filters = fromComplexPanelValue([panelRow(value)], leaves);

        expect(filters).toHaveLength(1);
        expect(filters[0].column_id).toBe("approved");
        expect(filters[0].filter_config).toEqual({
          filter_type: "boolean",
          filter_op: "equals",
          filter_value: expected,
        });
        expect(toComplexPanelValue(filters, leaves)).toMatchObject([
          { ...panelRow(String(expected)) },
        ]);
      });
    },
  );
});
