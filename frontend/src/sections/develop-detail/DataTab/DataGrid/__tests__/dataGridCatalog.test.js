import { describe, expect, it } from "vitest";
import {
  resolveColumns,
  toAgColumnState,
} from "src/sections/common/ux/oneview/columnLayout";
import { dataGridCatalog } from "../useDataGridLayout";
import { filterIsActive } from "../useDataToolbarView";

const CONFIG = [
  { id: "c1", name: "title", role: "metadata", is_visible: true },
  {
    id: "c2",
    name: "instruction",
    role: "input",
    is_visible: true,
    is_frozen: "left",
  },
  { id: "c3", name: "notes", role: "metadata", is_visible: false },
  { id: "e1", name: "Correct", origin_type: "evaluation" },
  { id: "e1r", name: "Correct reason", origin_type: "evaluation_reason" },
];

describe("Data grid column catalog (one view, A1-10)", () => {
  it("lists Row, Splits, the dataset's columns, Source, Updated", () => {
    const ids = dataGridCatalog(CONFIG, { hasSplits: true }).map((c) => c.id);
    expect(ids).toEqual([
      "system:order",
      "system:splits",
      "c1",
      "c2",
      "c3",
      "e1",
      "system:source",
      "system:updated",
    ]);
    // No splits → no Splits column; eval reasons ride with their eval.
    expect(dataGridCatalog(CONFIG).map((c) => c.id)).not.toContain(
      "system:splits",
    );
  });

  it("pins the first identifier or input when the server has no frozen column", () => {
    const cat = dataGridCatalog([
      { id: "id", name: "id", role: "metadata", is_visible: true },
      {
        id: "message",
        name: "customer_message",
        role: "input",
        is_visible: true,
      },
      { id: "other", name: "other", role: "metadata", is_visible: true },
    ]);
    const byId = Object.fromEntries(cat.map((c) => [c.id, c]));
    expect(byId.id.defaultPinned).toBe("left");
    expect(byId.message.defaultPinned).toBeUndefined();

    const inputOnly = dataGridCatalog([
      { id: "message", name: "customer_message", role: "input" },
      { id: "other", name: "other", role: "metadata" },
    ]);
    expect(inputOnly.find((c) => c.id === "message").defaultPinned).toBe(
      "left",
    );
  });

  it("takes defaults from the server config; the user layout decides", () => {
    const cat = dataGridCatalog(CONFIG, { hasSplits: true });
    const byId = Object.fromEntries(cat.map((c) => [c.id, c]));
    expect(byId["system:order"]).toMatchObject({
      locked: true,
      defaultPinned: "left",
    });
    expect(byId.c2.defaultPinned).toBe("left");
    expect(byId.c3.defaultVisible).toBe(false);
    expect(byId["system:updated"].defaultVisible).toBe(false);
    expect(byId["system:source"].pinnable).toBe(false);

    const def = resolveColumns(cat, {});
    expect(def.filter((c) => c.hidden).map((c) => c.id)).toEqual([
      "c3",
      "system:updated",
    ]);
    // A hidden + frozen user layout becomes AG column state.
    const mine = resolveColumns(cat, {
      visible: { c1: false, "system:updated": true },
      pinned: { c1: null },
    });
    const state = Object.fromEntries(
      toAgColumnState(mine).map((s) => [s.colId, s]),
    );
    expect(state.c1.hide).toBe(true);
    expect(state["system:updated"].hide).toBe(false);
    expect(state.c2.pinned).toBe("left");
  });

  it("counts only filters that have a value", () => {
    expect(filterIsActive({ filterConfig: { filterValue: "" } })).toBe(false);
    expect(filterIsActive({ filterConfig: { filterValue: [] } })).toBe(false);
    expect(filterIsActive({ filterConfig: { filterValue: ["a"] } })).toBe(true);
    expect(filterIsActive({ filterConfig: { filterValue: "x" } })).toBe(true);
    expect(filterIsActive({})).toBe(false);
  });
});
