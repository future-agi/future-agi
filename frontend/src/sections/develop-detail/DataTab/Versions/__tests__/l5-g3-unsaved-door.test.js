// options-truth L5 G3 (data:unsaved-rows): the selection bar's New
// experiment keeps unsaved rows; Save version & continue follows them into
// the saved version through the save's row_id_map.
import { describe, expect, it } from "vitest";
import { rowIdsAfterGate } from "../unsavedSelection";

describe("L5 G3 · unsaved rows through the selection-bar door", () => {
  const rows = [
    { id: "imp-1", unsaved: true },
    { id: "imp-2", unsaved: true },
    { id: "row-12", unsaved: false },
  ];
  it("Save version & continue: temp ids become the saved rows", () => {
    expect(
      rowIdsAfterGate(rows, {
        row_id_map: { "imp-1": "row-301", "imp-2": "row-302" },
      }),
    ).toEqual(["row-301", "row-302", "row-12"]);
  });
  it("Use the saved version without them: only rows it has", () => {
    expect(rowIdsAfterGate(rows, undefined)).toEqual(["row-12"]);
  });
  it("no pending changes: every row as selected", () => {
    expect(
      rowIdsAfterGate([{ id: "row-1", unsaved: false }], undefined),
    ).toEqual(["row-1"]);
  });
});
