/**
 * Regression tests for the Extract JSON Key column filter predicate.
 *
 * Tests the actual `isJsonColumn` from `../columnFilterUtils` (not a copy),
 * so any change to the real predicate is immediately exercised here. This
 * is the regression lock that round-2 of the review specifically asked for.
 *
 * Scope explicitly covers Nikhil's earlier feedback:
 *   - round 1: primitives must be excluded (no `isValidJson("hello")` slip)
 *   - round 2: array-shaped JSON columns must be excluded
 *   - round 2: empty / undefined schemas must not crash
 *   - round 3: contract pinned through the shared module so test cannot
 *              "drift away from the code" by re-declaring the predicate
 */
import { describe, it, expect } from "vitest";
import { isJsonColumn } from "../columnFilterUtils";

describe("isJsonColumn — the durable signal used by ExtractJsonKey dropdown", () => {
  describe("dataType-driven inclusion", () => {
    it("passes a column with dataType=json regardless of schemas", () => {
      expect(isJsonColumn({ field: "c", dataType: "json" }, {})).toBe(true);
    });

    it("passes a json-dataType column even when schemas is null/undefined", () => {
      expect(isJsonColumn({ field: "c", dataType: "json" }, null)).toBe(true);
      expect(isJsonColumn({ field: "c", dataType: "json" }, undefined)).toBe(true);
    });

    it("passes a json-dataType column even if its schema entry has empty keys", () => {
      const schemas = { c: { keys: [] } };
      expect(isJsonColumn({ field: "c", dataType: "json" }, schemas)).toBe(true);
    });
  });

  describe("schema-driven inclusion (text + api_call columns with JSON cells)", () => {
    it("passes a text column whose schema reports JSON keys", () => {
      const schemas = { c: { keys: ["id", "name"] } };
      expect(isJsonColumn({ field: "c", dataType: "text" }, schemas)).toBe(true);
    });

    it("passes any non-json dataType when the schema endpoint recorded keys", () => {
      // The schema endpoint is the durable signal — if it found keys, the
      // backend extractor will too (they share parse_json_safely on dev).
      const schemas = { c: { keys: ["k"] } };
      expect(isJsonColumn({ field: "c", dataType: "string" }, schemas)).toBe(true);
      expect(isJsonColumn({ field: "c", dataType: "api_call" }, schemas)).toBe(true);
    });
  });

  describe("exclusions", () => {
    it("rejects a text column with no schema entry at all", () => {
      expect(isJsonColumn({ field: "c", dataType: "text" }, {})).toBe(false);
    });

    it("rejects a text column whose schema entry has zero keys (array-shaped JSON)", () => {
      // The schema endpoint emits an entry with empty `keys` for array-of-X
      // columns because top-level arrays have no keys to extract. The
      // dropdown is for object-key extraction, so this must be excluded.
      const schemas = { c: { keys: [] } };
      expect(isJsonColumn({ field: "c", dataType: "text" }, schemas)).toBe(false);
    });

    it("rejects primitive-typed columns (number, boolean)", () => {
      expect(isJsonColumn({ field: "c", dataType: "number" }, {})).toBe(false);
      expect(isJsonColumn({ field: "c", dataType: "boolean" }, {})).toBe(false);
      expect(isJsonColumn({ field: "c", dataType: "float" }, {})).toBe(false);
    });

    it("rejects when schemas has the column but no keys field at all", () => {
      const schemas = { c: { name: "foo" } };
      expect(isJsonColumn({ field: "c", dataType: "text" }, schemas)).toBe(false);
    });

    it("rejects when schemas has the column but keys is undefined", () => {
      const schemas = { c: { keys: undefined } };
      expect(isJsonColumn({ field: "c", dataType: "text" }, schemas)).toBe(false);
    });
  });

  describe("resilience to undefined / loading state", () => {
    it("does not crash when jsonSchemas is undefined (hook not resolved yet)", () => {
      expect(isJsonColumn({ field: "c", dataType: "text" }, undefined)).toBe(false);
      expect(isJsonColumn({ field: "c", dataType: "json" }, undefined)).toBe(true);
    });

    it("does not crash when jsonSchemas is null", () => {
      expect(isJsonColumn({ field: "c", dataType: "text" }, null)).toBe(false);
    });

    it("does not crash when the column's field is missing from schemas", () => {
      const schemas = { otherCol: { keys: ["x"] } };
      expect(isJsonColumn({ field: "c", dataType: "text" }, schemas)).toBe(false);
    });
  });

  describe("return type", () => {
    // Round-3 nit: the predicate must return a strict boolean, not the
    // truthy `keys.length` integer. The `Boolean(...)` wrap in the source
    // makes this so. Locking it here.
    it("always returns a strict boolean (never a number)", () => {
      const schemas = { c: { keys: ["a", "b", "c"] } };
      const result = isJsonColumn({ field: "c", dataType: "text" }, schemas);
      expect(typeof result).toBe("boolean");
      expect(result).toBe(true);

      const result2 = isJsonColumn({ field: "c", dataType: "text" }, {});
      expect(typeof result2).toBe("boolean");
      expect(result2).toBe(false);
    });
  });
});
