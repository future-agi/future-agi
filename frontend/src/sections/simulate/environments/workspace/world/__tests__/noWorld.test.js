import { describe, expect, it } from "vitest";
import { hasNoWorld, worldTableCount } from "../world.constants";

const harnessStore = {
  capability: "harness",
  tables: [{ name: "harness_calls", rows: 2, internal: true }],
};

describe("hasNoWorld", () => {
  it("is true for a connect-only agent from the start of its build", () => {
    expect(
      hasNoWorld({ agentMode: "connect_only", buildStatus: "building" }),
    ).toBe(true);
  });

  it("is true for a ready environment whose only tables are the harness's own", () => {
    expect(
      hasNoWorld({
        detailReady: true,
        buildStatus: "ready",
        stores: [harnessStore],
      }),
    ).toBe(true);
  });

  it("is false while a code agent's world is still being written or the build stopped", () => {
    const env = { detailReady: true, agentMode: "environment_backed" };
    expect(hasNoWorld({ ...env, buildStatus: "building" })).toBe(false);
    expect(hasNoWorld({ ...env, buildStatus: "failed" })).toBe(false);
  });

  it("is false for a ready environment with a seeded table", () => {
    expect(
      hasNoWorld({
        detailReady: true,
        buildStatus: "ready",
        worldTables: [{ name: "rides", rows: 3 }],
      }),
    ).toBe(false);
  });
});

describe("worldTableCount", () => {
  it("counts the world's tables, not the harness's", () => {
    expect(
      worldTableCount({
        stores: [
          harnessStore,
          { capability: "world_db", tables: [{ name: "rides", rows: 3 }] },
        ],
      }),
    ).toBe(1);
  });

  it("is zero for a connect-only agent", () => {
    expect(
      worldTableCount({
        agentMode: "connect_only",
        worldTables: [{ name: "rides" }],
      }),
    ).toBe(0);
  });
});
