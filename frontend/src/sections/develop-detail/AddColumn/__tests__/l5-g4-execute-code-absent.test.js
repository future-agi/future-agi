// options-truth L5 G4 (owner the capability contract-10-22): Execute Custom Code is
// built but stays out of the Add column menu until its backend test is
// green; the seven dynamic column types are always offered.
import { describe, expect, it } from "vitest";
import {
  DynamicColumns,
  EXECUTE_CODE_COLUMN_OFFERED,
  RERUN_SCOPES,
} from "../common";

describe("Add column menu", () => {
  it("does not offer Execute Custom Code", () => {
    expect(EXECUTE_CODE_COLUMN_OFFERED).toBe(false);
    const text = JSON.stringify(DynamicColumns);
    expect(text).not.toMatch(/Execute Custom Code|executeCustomCode|python/i);
  });
  it("offers the seven dynamic column types", () => {
    expect(DynamicColumns.map((c) => c.label)).toEqual([
      "Run Prompt",
      "Retrieval",
      "Extract Entities",
      "Extract a JSON Key",
      "Classification",
      "API Calls",
      "Conditional Node",
    ]);
  });
  it("Save offers the three re-run scopes", () => {
    expect(RERUN_SCOPES.map((s) => s.value)).toEqual([
      "all",
      "changed",
      "failed",
    ]);
  });
});
