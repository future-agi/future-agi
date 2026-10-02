import { describe, expect, it } from "vitest";
import {
  BUILTIN_COLUMNS,
  DEFAULT_CONFIG,
  LOCKED_COLUMN_ID,
  MAX_COLUMN_ENTRIES,
  customNameFromId,
  isCustomColumnId,
  moveColumn,
  removeColumn,
  resolveColumns,
  toCustomColumnId,
  toggleColumn,
  validateConfig,
} from "../columnModel";

const DEFAULT_LABELS = [
  "Timestamp",
  "Model",
  "Provider",
  "Application",
  "Service",
  "Status",
  "Latency",
  "Cost",
  "Tokens",
  "Session ID",
];

const decl = (name) => ({ name, organization: "org-1" });

describe("columnModel ids and defaults", () => {
  it("exposes the ten existing built-in columns in the existing order (AC1/R43)", () => {
    expect(BUILTIN_COLUMNS.map((c) => c.label)).toEqual(DEFAULT_LABELS);
    expect(BUILTIN_COLUMNS.every((c) => c.id.startsWith("builtin:"))).toBe(
      true,
    );
    expect(DEFAULT_CONFIG).toEqual({
      v: 1,
      columns: BUILTIN_COLUMNS.map((c) => ({ id: c.id })),
      hidden: [],
    });
    expect(LOCKED_COLUMN_ID).toBe("builtin:startedAt");
  });

  it("namespaces custom ids so a property named model cannot collide (AC9)", () => {
    expect(toCustomColumnId("model")).toBe("metadata:model");
    expect(isCustomColumnId("metadata:model")).toBe(true);
    expect(isCustomColumnId("builtin:model")).toBe(false);
    expect(customNameFromId("metadata:tenant")).toBe("tenant");
    expect(customNameFromId("builtin:model")).toBeNull();
  });
});

describe("validateConfig (R14/R15/AC12)", () => {
  const valid = {
    v: 1,
    columns: [
      { id: "builtin:startedAt" },
      { id: "metadata:tenant" },
      { id: "builtin:model" },
    ],
    hidden: ["builtin:model"],
  };

  it("accepts a well-formed record and appends missing built-ins visible, in default order", () => {
    const result = validateConfig(valid);
    expect(result).not.toBeNull();
    expect(result.columns.slice(0, 3)).toEqual(valid.columns);
    expect(result.columns.map((c) => c.id)).toEqual([
      "builtin:startedAt",
      "metadata:tenant",
      "builtin:model",
      "builtin:provider",
      "builtin:application",
      "builtin:service",
      "builtin:statusCode",
      "builtin:latencyMs",
      "builtin:cost",
      "builtin:totalTokens",
      "builtin:sessionId",
    ]);
    expect(result.hidden).toEqual(["builtin:model"]);
  });

  it.each([
    ["null", null],
    ["string", "{}"],
    ["array", []],
    ["unknown version", { ...valid, v: 2 }],
    ["missing columns", { v: 1, hidden: [] }],
    ["empty columns", { v: 1, columns: [], hidden: [] }],
    [
      "duplicate ids",
      {
        v: 1,
        columns: [{ id: "builtin:startedAt" }, { id: "builtin:startedAt" }],
        hidden: [],
      },
    ],
    [
      "unknown builtin",
      {
        v: 1,
        columns: [{ id: "builtin:startedAt" }, { id: "builtin:nope" }],
        hidden: [],
      },
    ],
    ["unnamespaced id", { v: 1, columns: [{ id: "startedAt" }], hidden: [] }],
    [
      "locked column not first",
      {
        v: 1,
        columns: [{ id: "builtin:model" }, { id: "builtin:startedAt" }],
        hidden: [],
      },
    ],
    [
      "locked column missing",
      { v: 1, columns: [{ id: "builtin:model" }], hidden: [] },
    ],
    [
      "locked column hidden",
      {
        v: 1,
        columns: [{ id: "builtin:startedAt" }],
        hidden: ["builtin:startedAt"],
      },
    ],
    [
      "hidden id not in columns",
      {
        v: 1,
        columns: [{ id: "builtin:startedAt" }],
        hidden: ["builtin:model"],
      },
    ],
    [
      "hidden not an array",
      { v: 1, columns: [{ id: "builtin:startedAt" }], hidden: "builtin:model" },
    ],
    [
      "invalid metadata name characters",
      {
        v: 1,
        columns: [{ id: "builtin:startedAt" }, { id: "metadata:bad name" }],
        hidden: [],
      },
    ],
    [
      "too many entries",
      {
        v: 1,
        columns: [
          { id: "builtin:startedAt" },
          ...Array.from({ length: MAX_COLUMN_ENTRIES }, (_, i) => ({
            id: `metadata:p${i}`,
          })),
        ],
        hidden: [],
      },
    ],
  ])("rejects %s and returns null", (_label, raw) => {
    expect(validateConfig(raw)).toBeNull();
  });

  it("never persists anything but ids (R13)", () => {
    const result = validateConfig({
      v: 1,
      columns: [{ id: "builtin:startedAt", label: "x", value: "secret" }],
      hidden: [],
    });
    expect(result.columns[0]).toEqual({ id: "builtin:startedAt" });
  });
});

describe("resolveColumns (R7, J5, J6)", () => {
  it("renders the defaults with no declarations", () => {
    const { columns, stale } = resolveColumns({
      config: DEFAULT_CONFIG,
      declarations: [],
      status: "success",
    });
    expect(columns.map((c) => c.label)).toEqual(DEFAULT_LABELS);
    expect(stale).toEqual([]);
  });

  it("renders a declared custom column in config order with a key-derived heading", () => {
    const config = {
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: ["builtin:service"],
    };
    const { columns } = resolveColumns({
      config,
      declarations: [decl("tenant")],
      status: "success",
    });
    expect(columns.map((c) => c.label)).toEqual([
      ...DEFAULT_LABELS.filter((l) => l !== "Service"),
      "tenant",
    ]);
    const tenant = columns[columns.length - 1];
    expect(tenant).toMatchObject({
      id: "metadata:tenant",
      kind: "metadata",
      name: "tenant",
      sortable: false,
    });
  });

  it("holds custom columns back while declarations are pending or failed, without touching config (AC18)", () => {
    const config = {
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: [],
    };
    for (const status of ["pending", "error"]) {
      const { columns, stale, entries } = resolveColumns({
        config,
        declarations: [],
        status,
      });
      expect(columns.map((c) => c.id)).not.toContain("metadata:tenant");
      expect(stale).toEqual([]);
      expect(entries.custom).toEqual([]);
    }
  });

  it("marks undeclared saved ids stale only after a successful fetch (AC14)", () => {
    const config = {
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: [],
    };
    const { columns, stale, entries } = resolveColumns({
      config,
      declarations: [decl("tenant2")],
      status: "success",
    });
    expect(columns.map((c) => c.id)).not.toContain("metadata:tenant");
    expect(stale).toEqual(["metadata:tenant"]);
    expect(entries.custom.map((e) => e.id)).toEqual(["metadata:tenant2"]);
    expect(entries.custom[0].checked).toBe(false);
  });

  it("excludes the reserved application/service keys from custom entries (D3)", () => {
    const { entries } = resolveColumns({
      config: DEFAULT_CONFIG,
      declarations: [decl("application"), decl("service"), decl("tenant")],
      status: "success",
    });
    expect(entries.custom.map((e) => e.id)).toEqual(["metadata:tenant"]);
  });

  it("lists picker entries: built-ins in config order with locked Timestamp, customs in config order then declaration order", () => {
    const config = {
      v: 1,
      columns: [
        { id: "builtin:startedAt" },
        { id: "builtin:provider" },
        { id: "builtin:model" },
        { id: "metadata:zeta" },
        { id: "builtin:application" },
        { id: "builtin:service" },
        { id: "builtin:statusCode" },
        { id: "builtin:latencyMs" },
        { id: "builtin:cost" },
        { id: "builtin:totalTokens" },
        { id: "builtin:sessionId" },
      ],
      hidden: ["builtin:provider"],
    };
    const { entries, visibleCount, totalCount } = resolveColumns({
      config,
      declarations: [decl("alpha"), decl("zeta")],
      status: "success",
    });
    expect(entries.builtin.map((e) => e.label)).toEqual([
      "Timestamp",
      "Provider",
      "Model",
      "Application",
      "Service",
      "Status",
      "Latency",
      "Cost",
      "Tokens",
      "Session ID",
    ]);
    expect(entries.builtin[0]).toMatchObject({
      locked: true,
      checked: true,
      canMoveUp: false,
      canMoveDown: false,
    });
    expect(entries.builtin[1]).toMatchObject({
      checked: false,
      canMoveUp: false,
      canMoveDown: true,
    });
    expect(entries.builtin[9]).toMatchObject({ canMoveDown: false });
    expect(entries.custom.map((e) => e.id)).toEqual([
      "metadata:zeta",
      "metadata:alpha",
    ]);
    expect(entries.custom[0]).toMatchObject({
      checked: true,
      canMoveUp: false,
      canMoveDown: false,
    });
    expect(entries.custom[1]).toMatchObject({
      checked: false,
      canMoveUp: false,
      canMoveDown: false,
    });
    expect(visibleCount).toBe(10);
    expect(totalCount).toBe(12);
  });
});

describe("config mutations", () => {
  it("toggle hides a visible column keeping its slot, re-shows a hidden one and appends a new custom id", () => {
    let config = toggleColumn(DEFAULT_CONFIG, "builtin:provider");
    expect(config.hidden).toEqual(["builtin:provider"]);
    expect(config.columns).toEqual(DEFAULT_CONFIG.columns);
    config = toggleColumn(config, "builtin:provider");
    expect(config.hidden).toEqual([]);
    config = toggleColumn(config, "metadata:tenant");
    expect(config.columns[config.columns.length - 1]).toEqual({
      id: "metadata:tenant",
    });
    expect(config.hidden).toEqual([]);
  });

  it("toggle never hides the locked column (AC5)", () => {
    expect(toggleColumn(DEFAULT_CONFIG, LOCKED_COLUMN_ID)).toEqual(
      DEFAULT_CONFIG,
    );
  });

  it("move swaps with the neighbour of the same kind and never displaces the locked column", () => {
    const moved = moveColumn(DEFAULT_CONFIG, "builtin:provider", -1);
    expect(moved.columns.map((c) => c.id).slice(0, 3)).toEqual([
      "builtin:startedAt",
      "builtin:provider",
      "builtin:model",
    ]);
    expect(moveColumn(DEFAULT_CONFIG, "builtin:model", -1)).toEqual(
      DEFAULT_CONFIG,
    );
    expect(moveColumn(DEFAULT_CONFIG, LOCKED_COLUMN_ID, 1)).toEqual(
      DEFAULT_CONFIG,
    );
    expect(moveColumn(DEFAULT_CONFIG, "builtin:sessionId", 1)).toEqual(
      DEFAULT_CONFIG,
    );
    const withCustom = {
      v: 1,
      columns: [
        ...DEFAULT_CONFIG.columns,
        { id: "metadata:a" },
        { id: "metadata:b" },
      ],
      hidden: [],
    };
    const customMoved = moveColumn(withCustom, "metadata:b", -1);
    expect(customMoved.columns.slice(-2).map((c) => c.id)).toEqual([
      "metadata:b",
      "metadata:a",
    ]);
    expect(moveColumn(withCustom, "metadata:a", -1)).toEqual(withCustom);
    expect(moveColumn(withCustom, "metadata:missing", 1)).toEqual(withCustom);
  });

  it("removeColumn drops a stale id from columns and hidden but never the locked column", () => {
    const config = {
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:old" }],
      hidden: ["metadata:old"],
    };
    const removed = removeColumn(config, "metadata:old");
    expect(removed).toEqual(DEFAULT_CONFIG);
    expect(removeColumn(DEFAULT_CONFIG, LOCKED_COLUMN_ID)).toEqual(
      DEFAULT_CONFIG,
    );
  });
});
