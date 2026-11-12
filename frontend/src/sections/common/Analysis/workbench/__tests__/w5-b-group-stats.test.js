// W5 B1: per-group numbers (Δ vs baseline through runNumbers.compareRuns,
// the group's own pill, the cell text).
import { describe, expect, it } from "vitest";
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import { measureOptions } from "../workbenchModel";
import { buildPivot } from "../workbenchPivot";
import { deltaOn, groupDeltas } from "../groupDelta";
import { groupRowsPill, ownGroupPill, sameIds } from "../groupPill";
import { deltaLine, rangeText } from "../groupNumbers";

const PASS = measureOptions([], { k: 1 })[0];
const COST = measureOptions([]).find((m) => m.key === "cost");
const B = "base";
const C = "cand";
// 40 rows: difficulty easy / hard; the candidate fixes hard rows.
const rows = Array.from({ length: 40 }, (_, i) => {
  const hard = i % 2 === 1;
  const b = hard ? (i % 4 === 1 ? 1 : 0) : 1;
  const c = hard ? 1 : i % 10 === 0 ? 0 : 1;
  return {
    row_id: `r${i}`,
    n: i + 1,
    group_values: { difficulty: hard ? "hard" : "easy" },
    values: {},
    cells: {
      [B]: { trials: [b], cost_usd: [0.001 + i * 1e-5] },
      [C]: { trials: [c], cost_usd: [0.002 + i * 2e-5] },
    },
  };
});
const matrix = { rows };
const columns = [
  { key: B, label: "#1A", runIds: [B] },
  { key: C, label: "#2A", runIds: [C] },
];

describe("groupDeltas", () => {
  it("rate Δ per group equals runNumbers.compareRuns on the group's rows", () => {
    const pivot = buildPivot(matrix, {
      rows: "meta:difficulty",
      columns,
      measure: PASS,
      k: 1,
    });
    const d = groupDeltas(matrix, pivot, { baselineId: B, measure: PASS });
    ["easy", "hard"].forEach((g) => {
      const ids = rows
        .filter((r) => r.group_values.difficulty === g)
        .map((r) => r.row_id);
      const ref = compareRuns({
        matrix,
        baselineId: B,
        candidateId: C,
        scope: ids,
      });
      expect(d[g][C].delta).toBe(ref.deltaPts);
      expect(d[g][C].low).toBe(ref.range.lo);
      expect(d[g][C].significant).toBe(ref.significant);
      expect(d[g][B]).toBeUndefined();
    });
    expect(d.hard[C].significant).toBe(true);
  });
  it("numeric Δ is the paired mean difference", () => {
    const d = deltaOn(matrix, {
      baselineId: B,
      candidateId: C,
      measure: COST,
      rowIds: ["r0", "r1"],
    });
    expect(d.kind).toBe("mean");
    expect(d.delta).toBeCloseTo((0.002 - 0.001 + 0.00202 - 0.00101) / 2, 12);
  });
  it("no Δ for counts or without a baseline", () => {
    const tool = measureOptions([]).find((m) => m.key === "tool_errors");
    expect(
      deltaOn(matrix, { baselineId: B, candidateId: C, measure: tool }),
    ).toBeNull();
    const pivot = buildPivot(matrix, { rows: "none", columns, measure: PASS });
    expect(groupDeltas(matrix, pivot, { measure: PASS })).toEqual({});
  });
});

describe("the group's own pill", () => {
  const summary = {
    metadata_keys: ["difficulty", "tier"],
    metadata_values: { difficulty: ["easy", "hard"], tier: [1, 2] },
    splits: [{ id: "s1", name: "smoke" }],
  };
  it("metadata and split pills when the summary knows them", () => {
    expect(ownGroupPill("meta:difficulty", "hard", summary)).toEqual({
      type: "meta",
      key: "difficulty",
      op: "eq",
      value: "hard",
    });
    expect(ownGroupPill("meta:tier", "2", summary).value).toBe(2);
    expect(ownGroupPill("meta:split", "smoke", summary)).toEqual({
      type: "split",
      values: ["s1"],
    });
    expect(ownGroupPill("meta:split", "(no split)", summary).values).toEqual([
      "__none__",
    ]);
  });
  it("null for unknown keys, values or axes", () => {
    expect(ownGroupPill("meta:category", "billing", summary)).toBeNull();
    expect(ownGroupPill("meta:difficulty", "medium", summary)).toBeNull();
    expect(ownGroupPill("col:intent", "x", summary)).toBeNull();
  });
  it("the rows pill is labelled by the group", () => {
    const p = groupRowsPill("meta:difficulty", "hard", ["a", "b"]);
    expect(p.type).toBe("rows");
    expect(p.label).toBe("Difficulty: hard");
    expect(sameIds(["a", "b"], ["b", "a"])).toBe(true);
    expect(sameIds(["a"], ["a", "b"])).toBe(false);
  });
});

describe("cell text", () => {
  it("interval and Δ lines", () => {
    expect(rangeText(PASS, { low: 0.612, high: 0.851 })).toBe("61–85%");
    const t = deltaLine(
      PASS,
      {
        kind: "rate",
        delta: 12.3,
        low: 0.4,
        high: 24.1,
        significant: true,
        n: 30,
      },
      "#1A",
    );
    expect(t.short).toBe("Δ +12.3 pts (+0.4 to +24.1) · significant");
    expect(t.full).toContain("vs #1A");
    const n = deltaLine(PASS, {
      kind: "rate",
      delta: -2.9,
      low: -10.2,
      high: 4.6,
      significant: false,
      n: 30,
    });
    expect(n.short).toBe("Δ −2.9 pts (−10.2 to +4.6)");
  });
});
