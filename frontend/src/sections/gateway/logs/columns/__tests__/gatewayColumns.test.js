import { describe, expect, it } from "vitest";
import {
  BUILTIN_COLUMNS,
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

// Every built-in shown, in the canonical order (the default view hides three).
const PLAIN = Object.freeze({
  v: 1,
  columns: BUILTIN_COLUMNS.map((col) => ({ id: col.id })),
  hidden: [],
});

// A request's cost is the built-in Cost column alone: no Upstream cost or
// Markup column splits a credits charge.
const GATEWAY_LABELS = [
  "Billing mode",
  "Credential type",
  "Attempts",
  "Agent",
  "Step",
  "Run",
];

const ids = (config) => config.columns.map((c) => c.id);

const withGateway = (...gatewayIds) => ({
  v: 1,
  columns: [...PLAIN.columns, ...gatewayIds.map((id) => ({ id }))],
  hidden: [],
});

describe("gateway columns", () => {
  it("are six opt-in columns in their own namespace", () => {
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
    expect(ids(PLAIN).some(isGatewayColumnId)).toBe(false);
    const { columns } = resolveColumns({
      config: PLAIN,
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
      resolveGatewayColumns(PLAIN);
    expect(columns).toEqual([]);
    expect(entries.map((e) => e.label)).toEqual(GATEWAY_LABELS);
    expect(entries.every((e) => !e.checked && !e.inConfig)).toBe(true);
    expect(visibleCount).toBe(0);
    expect(totalCount).toBe(6);
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
    let config = toggleColumn(PLAIN, "gateway:attemptCount");
    expect(ids(config).at(-1)).toBe("gateway:attemptCount");
    expect(config.hidden).toEqual([]);
    config = toggleColumn(config, "gateway:attemptCount");
    expect(config.hidden).toEqual(["gateway:attemptCount"]);
  });

  it("toggle refuses a new gateway column at the cap", () => {
    const full = {
      v: 1,
      columns: [
        ...PLAIN.columns,
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
        ...PLAIN.columns.slice(0, 2),
        { id: "gateway:agentId" },
        ...PLAIN.columns.slice(2),
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
    expect(removeColumn(config, "gateway:agentRunId")).toEqual(PLAIN);
  });
});

describe("orderColumns", () => {
  it("merges resolved columns into config order", () => {
    const config = {
      v: 1,
      columns: [
        PLAIN.columns[0],
        { id: "gateway:agentRunId" },
        ...PLAIN.columns.slice(1),
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

// Credential type describes managed calls only. A deployment with no managed
// models, and no managed call in the loaded logs, is not offered it; a layout
// that saved it keeps it, unshown.
describe("Agent & billing columns where no managed call can be listed", () => {
  it("offers no Credential type column, and keeps a saved one in the layout", () => {
    const config = withGateway("gateway:credentialType", "gateway:agentId");
    const off = resolveGatewayColumns(config, { managed: false });
    expect(off.entries.map((e) => e.label)).toEqual([
      "Agent",
      "Billing mode",
      "Attempts",
      "Step",
      "Run",
    ]);
    expect(off.columns.map((c) => c.id)).toEqual(["gateway:agentId"]);
    expect(ids(config)).toContain("gateway:credentialType");

    const on = resolveGatewayColumns(config);
    expect(on.columns.map((c) => c.id)).toEqual([
      "gateway:credentialType",
      "gateway:agentId",
    ]);
    expect(on.entries.map((e) => e.label)).toEqual(
      expect.arrayContaining(["Credential type"]),
    );
  });

  // The removed Upstream cost and Markup columns: a saved layout that names
  // them keeps everything else (retiredColumns.test.jsx covers the table).
  it("drops a removed column from a saved layout and keeps the rest", () => {
    const config = validateConfig({
      ...withGateway("gateway:costMarkup", "gateway:agentId", "gateway:costUpstream"),
      hidden: ["gateway:costUpstream"],
    });
    expect(ids(config).filter(isGatewayColumnId)).toEqual(["gateway:agentId"]);
    expect(config.hidden).toEqual([]);
    expect(resolveGatewayColumns(config).entries.map((e) => e.label)).not.toEqual(
      expect.arrayContaining(["Upstream cost"]),
    );
  });
});
