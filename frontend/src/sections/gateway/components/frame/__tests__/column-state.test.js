import { afterEach, describe, expect, it } from "vitest";

import {
  loadColumnState,
  migrateColumnState,
  saveColumnState,
} from "../column-state";

const KEY = (k) => `gateway-grid:${k}`;

afterEach(() => window.localStorage.clear());

// A table whose saved layout key moves keeps the user's
// layout, carried over once, instead of resetting it.
describe("migrateColumnState", () => {
  const OLD = [{ colId: "a", width: 120, flex: null, sort: "asc" }];

  it("carries a layout saved under the old key to the new one, once", () => {
    saveColumnState("t-v1", OLD);
    migrateColumnState("t-v1", "t-v2", (state) =>
      state.map((col) => ({ ...col, moved: true })),
    );
    expect(loadColumnState("t-v2")).toEqual([{ ...OLD[0], moved: true }]);
    expect(window.localStorage.getItem(KEY("t-v1"))).toBeNull();
  });

  it("keeps a layout already saved under the new key", () => {
    saveColumnState("t-v1", OLD);
    saveColumnState("t-v2", [{ colId: "b" }]);
    migrateColumnState("t-v1", "t-v2", (state) => state);
    expect(loadColumnState("t-v2")).toEqual([{ colId: "b" }]);
  });

  it("does nothing when there is no old layout", () => {
    migrateColumnState("t-v1", "t-v2", (state) => state);
    expect(loadColumnState("t-v2")).toBeNull();
  });
});
