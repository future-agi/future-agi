import { describe, it, expect } from "vitest";
import {
  columnStateToHideMap,
  restampColumns,
  isColumnVisibilityDirty,
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
