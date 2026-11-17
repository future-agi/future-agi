// HO D7: an Expression column in the Data grid is computed, so its cells are
// not edited in place, and its header menu opens its own form first.
import { beforeEach, describe, expect, it } from "vitest";
import {
  EXPRESSION_ORIGIN,
  withExpressionColumnDef,
} from "../expressionColumnDef";
import { useExpressionColumnStore } from "../expressionColumnModel";

const baseMenu = (params) => [`Sort ${params.column}`, "separator", "Hide"];

describe("HO D7 · withExpressionColumnDef", () => {
  beforeEach(() => useExpressionColumnStore.getState().close());

  it("leaves every other column's definition as it is", () => {
    const def = {
      field: "c1",
      editable: true,
      col: { id: "c1", origin_type: "run_prompt" },
      mainMenuItems: baseMenu,
    };
    expect(withExpressionColumnDef(def)).toBe(def);
    expect(withExpressionColumnDef(undefined)).toBeUndefined();
    const noCol = { field: "system:row" };
    expect(withExpressionColumnDef(noCol)).toBe(noCol);
  });

  it.each([
    ["origin_type", { origin_type: EXPRESSION_ORIGIN }],
    ["originType", { originType: EXPRESSION_ORIGIN }],
  ])(
    "an expression column (%s) is not editable and opens its form",
    (_k, origin) => {
      const def = {
        field: "c9",
        editable: true,
        col: { id: "c9", ...origin },
        mainMenuItems: baseMenu,
      };
      const out = withExpressionColumnDef(def);
      expect(out.editable).toBe(false);
      expect(out.field).toBe("c9");
      const items = out.mainMenuItems({ column: "c9" });
      expect(items[0].name).toBe("Configure expression column");
      expect(items[1]).toBe("separator");
      // The column's own menu follows, unchanged and in order.
      expect(items.slice(2)).toEqual(baseMenu({ column: "c9" }));
      expect(useExpressionColumnStore.getState().open).toBeNull();
      items[0].action();
      expect(useExpressionColumnStore.getState().open).toEqual({
        editId: "c9",
      });
    },
  );

  it("keeps a definition with no menu function and still locks editing", () => {
    const def = {
      field: "c5",
      col: { id: "c5", origin_type: EXPRESSION_ORIGIN },
    };
    const out = withExpressionColumnDef(def);
    expect(out.editable).toBe(false);
    expect(out.mainMenuItems).toBeUndefined();
  });
});
