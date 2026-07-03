import { describe, it, expect } from "vitest";
import {
  columnStateToHideMap,
  columnStateToOrder,
  reorderColumns,
  restampColumns,
  applySavedColumns,
  isColumnVisibilityDirty,
  isColumnOrderDirty,
} from "../savedViewColumns";

describe("columnStateToHideMap", () => {
  it("maps colId -> hide boolean", () => {
    expect(
      columnStateToHideMap([
        { colId: "a", hide: true },
        { colId: "b", hide: false },
      ]),
    ).toEqual({ a: true, b: false });
  });

  it("coerces a missing hide flag to false", () => {
    expect(columnStateToHideMap([{ colId: "a" }])).toEqual({ a: false });
  });

  it("ignores entries without a colId and non-array input", () => {
    expect(columnStateToHideMap([{ hide: true }, null])).toEqual({});
    expect(columnStateToHideMap(undefined)).toEqual({});
  });
});

describe("restampColumns", () => {
  const makeCols = () => ({
    "primary-trace": [
      { id: "a", isVisible: true },
      { id: "b", isVisible: true },
    ],
  });

  it("hides a column the saved view marks hidden", () => {
    const cols = makeCols();
    const next = restampColumns(cols, { b: true });
    expect(next["primary-trace"][1].isVisible).toBe(false);
    expect(next["primary-trace"][0].isVisible).toBe(true);
  });

  it("shows a column the saved view marks visible", () => {
    const cols = {
      "primary-trace": [{ id: "a", isVisible: false }],
    };
    const next = restampColumns(cols, { a: false });
    expect(next["primary-trace"][0].isVisible).toBe(true);
  });

  // TH-5919: the deselect must survive the saved-view re-stamp.
  it("does not revert a user-toggled column", () => {
    const cols = {
      // user just hid "b"; saved view still wants it visible
      "primary-trace": [
        { id: "a", isVisible: true },
        { id: "b", isVisible: false },
      ],
    };
    const next = restampColumns(cols, { a: false, b: false }, new Set(["b"]));
    expect(next["primary-trace"][1].isVisible).toBe(false);
  });

  it("still stamps non-toggled columns when others are exempt", () => {
    const cols = makeCols();
    const next = restampColumns(cols, { a: true, b: true }, new Set(["b"]));
    expect(next["primary-trace"][0].isVisible).toBe(false);
    expect(next["primary-trace"][1].isVisible).toBe(true);
  });

  it("returns the same reference when nothing changed (no re-render churn)", () => {
    const cols = makeCols();
    expect(restampColumns(cols, { a: false, b: false })).toBe(cols);
  });

  it("keeps untouched slot arrays referentially stable", () => {
    const cols = {
      "primary-trace": [{ id: "a", isVisible: true }],
      "primary-spans": [{ id: "x", isVisible: true }],
    };
    const next = restampColumns(cols, { a: true });
    expect(next["primary-spans"]).toBe(cols["primary-spans"]);
    expect(next["primary-trace"]).not.toBe(cols["primary-trace"]);
  });

  it("ignores ids the hideMap does not mention", () => {
    const cols = makeCols();
    expect(restampColumns(cols, { c: true })).toBe(cols);
  });

  it("returns the input untouched when columnsObj or hideMap is missing", () => {
    const cols = makeCols();
    expect(restampColumns(cols, null)).toBe(cols);
    expect(restampColumns(null, { a: true })).toBe(null);
  });
});

describe("isColumnVisibilityDirty", () => {
  const columnState = [
    { colId: "a", hide: false },
    { colId: "b", hide: false },
  ];

  it("is dirty when a column was hidden vs the saved view", () => {
    const cols = [
      { id: "a", isVisible: true },
      { id: "b", isVisible: false },
    ];
    expect(isColumnVisibilityDirty(cols, columnState)).toBe(true);
  });

  it("is clean when visibility matches the saved view", () => {
    const cols = [
      { id: "a", isVisible: true },
      { id: "b", isVisible: true },
    ];
    expect(isColumnVisibilityDirty(cols, columnState)).toBe(false);
  });

  it("treats undefined isVisible as visible", () => {
    const cols = [{ id: "a" }, { id: "b" }];
    expect(isColumnVisibilityDirty(cols, columnState)).toBe(false);
  });

  it("ignores custom columns", () => {
    const cols = [{ id: "a", isVisible: false, groupBy: "Custom Columns" }];
    expect(isColumnVisibilityDirty(cols, [{ colId: "a", hide: false }])).toBe(
      false,
    );
  });

  it("ignores columns the baseline does not know about", () => {
    const cols = [{ id: "z", isVisible: false }];
    expect(isColumnVisibilityDirty(cols, columnState)).toBe(false);
  });

  it("is clean when there is no saved columnState", () => {
    const cols = [{ id: "a", isVisible: false }];
    expect(isColumnVisibilityDirty(cols, undefined)).toBe(false);
  });
});

describe("columnStateToOrder", () => {
  it("returns colIds in array order", () => {
    expect(
      columnStateToOrder([{ colId: "a" }, { colId: "b" }, { colId: "c" }]),
    ).toEqual(["a", "b", "c"]);
  });

  it("drops falsy colIds and handles non-arrays", () => {
    expect(columnStateToOrder([{ colId: "a" }, {}, null])).toEqual(["a"]);
    expect(columnStateToOrder(undefined)).toEqual([]);
  });
});

describe("reorderColumns — flat array", () => {
  it("reorders to match the given order", () => {
    expect(
      reorderColumns(
        [{ id: "a" }, { id: "b" }, { id: "c" }],
        ["c", "a", "b"],
      ).map((c) => c.id),
    ).toEqual(["c", "a", "b"]);
  });

  it("trails ids absent from order, preserving their relative position", () => {
    const arr = [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }];
    expect(reorderColumns(arr, ["c", "a"]).map((c) => c.id)).toEqual([
      "c",
      "a",
      "b",
      "d",
    ]);
  });

  it("returns the SAME reference when already in order (no-loop guard)", () => {
    const arr = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(reorderColumns(arr, ["a", "b", "c"])).toBe(arr);
  });

  it("returns the input on an empty order or null columns", () => {
    const arr = [{ id: "a" }];
    expect(reorderColumns(arr, [])).toBe(arr);
    expect(reorderColumns(null, ["a"])).toBe(null);
  });

  it("positions a custom-columns group by its id", () => {
    const arr = [
      { id: "a" },
      { id: "custom1", groupBy: "Custom Columns" },
      { id: "b" },
    ];
    expect(reorderColumns(arr, ["custom1", "a", "b"]).map((c) => c.id)).toEqual(
      ["custom1", "a", "b"],
    );
  });
});

describe("reorderColumns — slot object", () => {
  it("reorders every slot", () => {
    const obj = {
      "primary-trace": [{ id: "a" }, { id: "b" }],
      "compare-trace": [{ id: "a" }, { id: "b" }],
    };
    const next = reorderColumns(obj, ["b", "a"]);
    expect(next["primary-trace"].map((c) => c.id)).toEqual(["b", "a"]);
    expect(next["compare-trace"].map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("returns the SAME object reference when nothing changed", () => {
    const obj = { "primary-trace": [{ id: "a" }, { id: "b" }] };
    expect(reorderColumns(obj, ["a", "b"])).toBe(obj);
  });

  it("keeps an unchanged slot referentially stable", () => {
    const slotA = [{ id: "a" }, { id: "b" }];
    const slotB = [{ id: "x" }, { id: "y" }];
    const obj = { a: slotA, b: slotB };
    const next = reorderColumns(obj, ["b", "a"]);
    expect(next.a).not.toBe(slotA);
    expect(next.b).toBe(slotB);
  });
});

describe("applySavedColumns", () => {
  it("applies visibility AND order in one pass", () => {
    const obj = {
      "primary-trace": [
        { id: "a", isVisible: true },
        { id: "b", isVisible: true },
        { id: "c", isVisible: true },
      ],
    };
    const columnState = [
      { colId: "c", hide: false },
      { colId: "a", hide: true },
      { colId: "b", hide: false },
    ];
    const next = applySavedColumns(obj, columnState);
    expect(next["primary-trace"].map((c) => c.id)).toEqual(["c", "a", "b"]);
    expect(next["primary-trace"].find((c) => c.id === "a").isVisible).toBe(
      false,
    );
  });
});

describe("isColumnOrderDirty", () => {
  const saved = [{ colId: "a" }, { colId: "b" }, { colId: "c" }];

  it("is clean when the order matches (incl. drag-then-back end-state)", () => {
    expect(
      isColumnOrderDirty([{ id: "a" }, { id: "b" }, { id: "c" }], saved),
    ).toBe(false);
  });

  it("is dirty when columns are reordered", () => {
    expect(
      isColumnOrderDirty([{ id: "b" }, { id: "a" }, { id: "c" }], saved),
    ).toBe(true);
  });

  it("compares only the intersection (extra/missing cols don't dirty)", () => {
    expect(
      isColumnOrderDirty([{ id: "a" }, { id: "x" }, { id: "b" }], saved),
    ).toBe(false);
  });

  it("is dirty when the intersection order differs despite extra cols", () => {
    expect(
      isColumnOrderDirty([{ id: "b" }, { id: "x" }, { id: "a" }], saved),
    ).toBe(true);
  });

  it("is clean for a non-array columnState", () => {
    expect(isColumnOrderDirty([{ id: "a" }], undefined)).toBe(false);
  });

  it("ignores hidden cols so a custom col saved between hidden cols clears on drag-back (TH-6119)", () => {
    // Saved order wedges `cust` between two hidden columns — unreachable by drag.
    const savedWithHidden = [
      { colId: "a" },
      { colId: "b" },
      { colId: "hidden1" },
      { colId: "hidden2" },
      { colId: "cust" },
      { colId: "c" },
    ];
    // `cust` at its visible-original spot; hidden1/2 hidden → visible order matches.
    const current = [
      { id: "a" },
      { id: "b" },
      { id: "cust" },
      { id: "hidden1", isVisible: false },
      { id: "hidden2", isVisible: false },
      { id: "c" },
    ];
    expect(isColumnOrderDirty(current, savedWithHidden)).toBe(false);
  });

  it("is still dirty when a visible col is genuinely moved among visible cols", () => {
    const savedWithHidden = [
      { colId: "a" },
      { colId: "b" },
      { colId: "hidden1" },
      { colId: "cust" },
      { colId: "c" },
    ];
    // `cust` moved before `b` → visible order [a, cust, b, c] ≠ saved [a, b, cust, c].
    const current = [
      { id: "a" },
      { id: "cust" },
      { id: "b" },
      { id: "hidden1", isVisible: false },
      { id: "c" },
    ];
    expect(isColumnOrderDirty(current, savedWithHidden)).toBe(true);
  });
});
