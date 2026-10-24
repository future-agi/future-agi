import { describe, expect, it } from "vitest";
import {
  BUILTIN_COLUMNS,
  DEFAULT_CONFIG,
  MAX_COLUMN_ENTRIES,
  moveColumn,
  orderColumns,
  removeColumn,
  resolveColumns,
  resolveGatewayColumns,
  toggleColumn,
  validateConfig,
} from "../columnModel";
import { GATEWAY_COLUMNS, isGatewayColumnId } from "../gatewayColumns";

const GATEWAY_LABELS = [
  "Billing mode",
  "Credential type",
  "Upstream cost",
  "Markup",
  "Attempts",
  "Agent",
  "Step",
  "Run",
];

const ids = (config) => config.columns.map((c) => c.id);

const withGateway = (...gatewayIds) => ({
  v: 1,
  columns: [...DEFAULT_CONFIG.columns, ...gatewayIds.map((id) => ({ id }))],
  hidden: [],
});

describe("gateway columns", () => {
  it("are eight opt-in columns in their own namespace", () => {
    expect(GATEWAY_COLUMNS.map((c) => c.label)).toEqual(GATEWAY_LABELS);
    expect(GATEWAY_COLUMNS.every((c) => c.id.startsWith("gateway:"))).toBe(
      true,
    );
    expect(GATEWAY_COLUMNS.every((c) => c.kind === "gateway")).toBe(true);
    expect(isGatewayColumnId("gateway:agentRunId")).toBe(true);
    expect(isGatewayColumnId("builtin:model")).toBe(false);
    expect(isGatewayColumnId("metadata:gateway")).toBe(false);
  });

  it("leave the default table and the ten built-ins as they were", () => {
    expect(BUILTIN_COLUMNS).toHaveLength(10);
    expect(ids(DEFAULT_CONFIG).some(isGatewayColumnId)).toBe(false);
    const { columns } = resolveColumns({
      config: DEFAULT_CONFIG,
      declarations: [],
      status: "success",
    });
    expect(columns).toHaveLength(10);
  });
});

describe("validateConfig with gateway ids", () => {
  it("keeps saved gateway ids and their hidden state", () => {
    const raw = {
      ...withGateway("gateway:agentRunId", "gateway:billingMode"),
      hidden: ["gateway:billingMode"],
    };
    expect(validateConfig(raw)).toEqual(raw);
  });

  it("never adds gateway ids a saved config lacks", () => {
    const result = validateConfig({
      v: 1,
      columns: [{ id: "builtin:startedAt" }],
      hidden: [],
    });
    expect(ids(result).some(isGatewayColumnId)).toBe(false);
    expect(result.columns).toHaveLength(10);
  });

  it("rejects an unknown gateway id", () => {
    expect(validateConfig(withGateway("gateway:nope"))).toBeNull();
  });
});

describe("resolveGatewayColumns", () => {
  it("lists every gateway column, unchecked, for a config without them", () => {
    const { columns, entries, visibleCount, totalCount } =
      resolveGatewayColumns(DEFAULT_CONFIG);
    expect(columns).toEqual([]);
    expect(entries.map((e) => e.label)).toEqual(GATEWAY_LABELS);
    expect(entries.every((e) => !e.checked && !e.inConfig)).toBe(true);
    expect(visibleCount).toBe(0);
    expect(totalCount).toBe(8);
  });

  it("shows saved ones in config order, hidden ones unchecked, the rest after", () => {
    const config = {
      ...withGateway("gateway:agentRunId", "gateway:billingMode"),
      hidden: ["gateway:billingMode"],
    };
    const { columns, entries, visibleCount } = resolveGatewayColumns(config);
    expect(columns.map((c) => c.label)).toEqual(["Run"]);
    expect(entries.slice(0, 2)).toMatchObject([
      { id: "gateway:agentRunId", checked: true, inConfig: true },
      { id: "gateway:billingMode", checked: false, inConfig: true },
    ]);
    expect(entries.slice(2).map((e) => e.label)).toEqual(
      GATEWAY_LABELS.filter((l) => l !== "Run" && l !== "Billing mode"),
    );
    expect(entries[0]).toMatchObject({ canMoveUp: false, canMoveDown: true });
    expect(entries[1]).toMatchObject({ canMoveUp: true, canMoveDown: false });
    expect(entries[2]).toMatchObject({ canMoveUp: false, canMoveDown: false });
    expect(visibleCount).toBe(1);
  });
});

describe("config mutations with gateway ids", () => {
  it("toggle adds a gateway column at the end, visible, and hides it again", () => {
    let config = toggleColumn(DEFAULT_CONFIG, "gateway:attemptCount");
    expect(ids(config).at(-1)).toBe("gateway:attemptCount");
    expect(config.hidden).toEqual([]);
    config = toggleColumn(config, "gateway:attemptCount");
    expect(config.hidden).toEqual(["gateway:attemptCount"]);
  });

  it("toggle refuses a new gateway column at the cap", () => {
    const full = {
      v: 1,
      columns: [
        ...DEFAULT_CONFIG.columns,
        ...Array.from({ length: MAX_COLUMN_ENTRIES - 10 }, (_, i) => ({
          id: `metadata:p${i}`,
        })),
      ],
      hidden: [],
    };
    expect(toggleColumn(full, "gateway:agentId")).toBe(full);
  });

  it("moves a gateway column only among gateway columns", () => {
    const config = {
      v: 1,
      columns: [
        ...DEFAULT_CONFIG.columns.slice(0, 2),
        { id: "gateway:agentId" },
        ...DEFAULT_CONFIG.columns.slice(2),
        { id: "metadata:tenant" },
        { id: "gateway:agentStep" },
      ],
      hidden: [],
    };
    const movable = new Set([
      ...resolveColumns({
        config,
        declarations: [{ name: "tenant" }],
        status: "success",
      }).movableIds,
      ...resolveGatewayColumns(config).movableIds,
    ]);

    // A built-in move steps over the gateway column between built-ins.
    const builtinMoved = moveColumn(config, "builtin:model", 1, movable);
    expect(ids(builtinMoved).slice(0, 4)).toEqual([
      "builtin:startedAt",
      "builtin:provider",
      "gateway:agentId",
      "builtin:model",
    ]);

    // A gateway move steps over built-ins and customs to the next gateway id.
    const gatewayMoved = moveColumn(config, "gateway:agentId", 1, movable);
    expect(ids(gatewayMoved)[2]).toBe("gateway:agentStep");
    expect(ids(gatewayMoved).at(-1)).toBe("gateway:agentId");

    // ...and never past the end of its group.
    expect(moveColumn(config, "gateway:agentStep", 1, movable)).toBe(config);
  });

  it("reset and remove drop gateway columns like any other", () => {
    const config = withGateway("gateway:agentRunId");
    expect(removeColumn(config, "gateway:agentRunId")).toEqual(DEFAULT_CONFIG);
  });
});

describe("orderColumns", () => {
  it("merges resolved columns into config order", () => {
    const config = {
      v: 1,
      columns: [
        DEFAULT_CONFIG.columns[0],
        { id: "gateway:agentRunId" },
        ...DEFAULT_CONFIG.columns.slice(1),
        { id: "metadata:tenant" },
      ],
      hidden: [],
    };
    const resolved = resolveColumns({
      config,
      declarations: [{ name: "tenant" }],
      status: "success",
    });
    const merged = orderColumns(config, [
      ...resolved.columns,
      ...resolveGatewayColumns(config).columns,
    ]);
    expect(merged.map((c) => c.label)).toEqual([
      "Timestamp",
      "Run",
      ...BUILTIN_COLUMNS.slice(1).map((c) => c.label),
      "tenant",
    ]);
  });
});
