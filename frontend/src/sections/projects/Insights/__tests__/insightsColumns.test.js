import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  availableColumns,
  defaultColumnKeys,
  readColumnPrefs,
  toggleColumn,
  visibleColumns,
  writeColumnPrefs,
} from "../insightsColumns";

const attributes = [
  { key: "asked_for_human", type: "boolean", description: "Asked for a human" },
];

// Node's own Web Storage globals can shadow jsdom's, so each test installs a
// plain in-memory one (or one that throws, like a blocked browser).
const memoryStorage = () => {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    clear: () => data.clear(),
  };
};
const blockedStorage = () => ({
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
});

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("availableColumns", () => {
  it("fits the unit, the attributes and whether there is a previous run", () => {
    const sessions = availableColumns({
      unit: "sessions",
      attributes,
      hasChanges: true,
    }).map((c) => c.key);
    expect(sessions).toEqual([
      "share",
      "users",
      "notable",
      "change",
      "count",
      "failure",
      "unresolved",
      "sentiment",
      "latency",
      "cost",
      "errors",
      "attr:asked_for_human",
    ]);
    const traces = availableColumns({ unit: "traces" }).map((c) => c.key);
    expect(traces).toContain("sessions");
    expect(traces).not.toContain("change");
    const users = availableColumns({ unit: "users" }).map((c) => c.key);
    expect(users).not.toContain("users");
  });

  it("names the count after the unit and the attribute after its key", () => {
    const columns = availableColumns({ unit: "sessions", attributes });
    expect(columns.find((c) => c.key === "count").label).toBe("Sessions");
    expect(columns.find((c) => c.key === "attr:asked_for_human")).toMatchObject(
      {
        label: "Asked for human",
        attribute: "asked_for_human",
        attributeType: "boolean",
      },
    );
  });
});

describe("visible columns and prefs", () => {
  const columns = availableColumns({
    unit: "sessions",
    attributes,
    hasChanges: true,
  });

  it("shows Share, Users affected, Stands out and Change by default", () => {
    expect(defaultColumnKeys(columns)).toEqual([
      "share",
      "users",
      "notable",
      "change",
    ]);
    expect(visibleColumns(columns, null).map((c) => c.key)).toEqual(
      defaultColumnKeys(columns),
    );
  });

  it("toggles columns on and off and keeps the menu's order", () => {
    let prefs = toggleColumn(columns, null, "attr:asked_for_human");
    prefs = toggleColumn(columns, prefs, "unresolved");
    prefs = toggleColumn(columns, prefs, "users");
    expect(visibleColumns(columns, prefs).map((c) => c.key)).toEqual([
      "share",
      "notable",
      "change",
      "unresolved",
      "attr:asked_for_human",
    ]);
  });

  it("lets a default column appear on a later run when it was never touched", () => {
    const first = availableColumns({ unit: "sessions" });
    const prefs = toggleColumn(first, null, "sentiment");
    const later = availableColumns({ unit: "sessions", hasChanges: true });
    expect(visibleColumns(later, prefs).map((c) => c.key)).toContain("change");
  });

  it("persists the choice per report and survives broken storage", () => {
    writeColumnPrefs("r1", { on: ["sentiment"], off: ["users"] });
    expect(readColumnPrefs("r1")).toEqual({
      on: ["sentiment"],
      off: ["users"],
    });
    expect(readColumnPrefs("r2")).toBeNull();
    localStorage.setItem("insights-columns:r3", "{not json");
    expect(readColumnPrefs("r3")).toBeNull();
    vi.stubGlobal("localStorage", blockedStorage());
    expect(readColumnPrefs("r1")).toBeNull();
    expect(() => writeColumnPrefs("r1", { on: [] })).not.toThrow();
  });
});
