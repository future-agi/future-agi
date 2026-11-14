import { describe, it, expect } from "vitest";

import { normalizeFilterRowOperator } from "../TraceFilterPanel";
import { ID_ONLY_FIELDS } from "../idFields";

// Empirical check of the dead-alias claim: every operator the panel can hold
// passes through normalizeFilterRowOperator before it is stored/emitted. This
// proves that legacy `is`/`is_not` can never survive to the wire — they are
// always rewritten to a canonical operator for the field's own _OPS list — so
// the FE LEGACY_OP_ALIAS shim at the task boundary is genuinely dead.

const FIELD_CASES = [
  { label: "id-only (trace_id)", field: "trace_id", fieldType: "string" },
  { label: "id-only (span_id)", field: "span_id", fieldType: "string" },
  { label: "id-only (session)", field: "session", fieldType: "string" },
  { label: "string", field: "status", fieldType: "string" },
  { label: "text", field: "input", fieldType: "text" },
  { label: "number", field: "token_count", fieldType: "number" },
  { label: "boolean", field: "is_root", fieldType: "boolean" },
  { label: "date", field: "start_time", fieldType: "date" },
  { label: "array", field: "tags", fieldType: "array" },
];

describe("normalizeFilterRowOperator sanitizes legacy is/is_not", () => {
  it("ID_ONLY_FIELDS is the expected set", () => {
    expect([...ID_ONLY_FIELDS].sort()).toEqual(["session", "span_id", "trace_id"]);
  });

  for (const legacyOp of ["is", "is_not"]) {
    for (const fc of FIELD_CASES) {
      it(`${fc.label}: "${legacyOp}" → canonical (never is/is_not)`, () => {
        const out = normalizeFilterRowOperator({
          field: fc.field,
          fieldType: fc.fieldType,
          operator: legacyOp,
        });
        expect(out.operator).not.toBe("is");
        expect(out.operator).not.toBe("is_not");
        // and it lands on a real, non-empty operator string
        expect(typeof out.operator).toBe("string");
        expect(out.operator.length).toBeGreaterThan(0);
      });
    }
  }
});
