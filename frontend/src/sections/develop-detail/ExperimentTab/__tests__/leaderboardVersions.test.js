// R4 lane Rb MUST 7 (D22, D26; §C10, §C11): the leaderboard's versions
// and numbers come from runNumbers / provenanceModel, its sentences carry
// no digit literals, and the headline's pts equal compareRuns for the
// same pair (what the Compare chain prints).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import {
  bestLine,
  identityOf,
  leaderboardNumbers,
  mergeMatrices,
  successFrom,
  versionHeadline,
} from "../leaderboardVersions";

const agentRun = (id, label, version, { draft = false, at = 0 } = {}) => ({
  run_id: id,
  label,
  trials: 2,
  metrics: { cost_usd: 0.08 },
  created_at: new Date(Date.UTC(2026, 9, 1 + at)).toISOString(),
  config: {
    kind: "agent",
    agent: {
      graph_id: "g-support",
      name: "Support Agent",
      version,
      status: draft ? "draft" : "active",
    },
  },
});

const RUNS = [
  agentRun("v2", "#1A", "v2", { at: 0 }),
  agentRun("v3", "#2B", "v3", { at: 1 }),
  agentRun("v31", "#2G", "v3.1", { draft: true, at: 2 }),
];

// Four rows, two tries each. v3.1 fixes rows 1-2 and breaks row 4.
const cell = (a, b) => ({ trials: [a, b], cost_usd: [0.01, 0.01] });
const MATRIX = {
  rows: [
    {
      id: "r1",
      n: 1,
      cells: { v2: cell(0, 0), v3: cell(0, 0), v31: cell(1, 1) },
    },
    {
      id: "r2",
      n: 2,
      cells: { v2: cell(1, 0), v3: cell(0, 1), v31: cell(1, 1) },
    },
    {
      id: "r3",
      n: 3,
      cells: { v2: cell(1, 1), v3: cell(1, 1), v31: cell(1, 1) },
    },
    {
      id: "r4",
      n: 4,
      cells: { v2: cell(1, 1), v3: cell(1, 1), v31: cell(0, 0) },
    },
  ],
};

const clone = (m) => JSON.parse(JSON.stringify(m));

describe("identity and the version headline", () => {
  it("names a run by its agent and version", () => {
    expect(identityOf(RUNS[2]).title).toBe("Support Agent v3.1 (draft)");
    expect(identityOf({ run_id: "x", config: { kind: "prompt" } })).toBe(null);
  });

  it("chips the latest and the previous version", () => {
    const n = leaderboardNumbers(RUNS, MATRIX);
    expect(n.chips).toEqual({ v31: "Latest version", v3: "Previous version" });
    expect(n.total).toBe(3);
  });

  it("headline pts and scope equal compareRuns for the same pair", () => {
    const n = leaderboardNumbers(RUNS, MATRIX);
    const c = compareRuns({
      matrix: MATRIX,
      baselineId: "v3",
      candidateId: "v31",
      scope: "all",
      labels: { v2: "#1A", v3: "#2B", v31: "#2G" },
      noun: "task",
    });
    const h = versionHeadline(n, RUNS);
    expect(h.primary).toEqual({
      label: "Compare latest vs previous",
      kind: "pair",
    });
    expect(n.headlineCmp.deltaPts).toBe(c.deltaPts);
    expect(h.text).toContain(c.scopeText);
    expect(h.text.startsWith("Support Agent v3.1 (draft) vs v3: ")).toBe(true);
  });

  it("one version only: says so and offers best vs baseline", () => {
    const one = [RUNS[1]];
    const h = versionHeadline(leaderboardNumbers(one, MATRIX), one);
    expect(h.text).toBe("Only one version of Support Agent ran here (v3).");
    expect(h.primary.kind).toBe("best");
  });

  it("one version each: names the baseline's agent", () => {
    const prompt = {
      run_id: "p1",
      label: "#1A",
      config: {
        kind: "prompt",
        prompt: { id: "p", name: "Reply", version: "V1" },
      },
    };
    const runs = [prompt, RUNS[1]];
    const n = leaderboardNumbers(runs, MATRIX);
    expect(versionHeadline(n, runs, { baselineRun: RUNS[1] }).text).toBe(
      "Only one version of Support Agent ran here (v3).",
    );
    expect(versionHeadline(n, runs, { baselineRun: prompt }).text).toBe(
      "Only one version of Reply ran here (V1).",
    );
  });

  it("marks a run worse than its previous version (Matrix rule)", () => {
    const worse = clone(MATRIX);
    // v3.1 now fails rows 1-3 too: worse than v3 overall.
    ["r1", "r2", "r3"].forEach((id) => {
      worse.rows.find((r) => r.id === id).cells.v31 = cell(0, 0);
    });
    const n = leaderboardNumbers(RUNS, worse);
    // IT-R2 G2 P5: the previous version, never `baseline`.
    expect(n.worse.v31.text).toMatch(/^▼ −\d+\.\d pts vs \S+$/);
    expect(n.worse.v31.long).toMatch(
      /^\S+ vs \S+ v3 \(previous version\): −\d+\.\d pts · \d+ tasks? worse$/,
    );
    expect(n.worse.v3).toBeUndefined();
  });
});

describe("Best and Best value use every run", () => {
  it("labels the scope when a filter hides runs", () => {
    const n = leaderboardNumbers(RUNS, MATRIX);
    const plain = bestLine(n, { shown: 3, filtered: false });
    expect(plain.startsWith("Best overall: ")).toBe(true);
    const filtered = bestLine(n, { shown: 1, filtered: true });
    expect(filtered.startsWith("Best overall (all 3 runs): ")).toBe(true);
    expect(filtered.endsWith("(the filter hides 2)")).toBe(true);
  });

  it("a run without reported cost is never Best value", () => {
    const n = leaderboardNumbers(RUNS, MATRIX);
    expect(n.bestValue).not.toBe(null);
    const noCost = RUNS.map((r) =>
      r.run_id === n.bestValue.id ? { ...r, metrics: {} } : r,
    );
    const m = leaderboardNumbers(noCost, MATRIX);
    expect(m.bestValue?.id).not.toBe(n.bestValue.id);
    expect(m.byRun[n.bestValue.id].value.text).toBe("— · cost not reported");
  });

  it("the table's success rate is runRate's", () => {
    const n = leaderboardNumbers(RUNS, MATRIX);
    const s = successFrom(n, RUNS[2]);
    expect(s.text).toBe(n.byRun.v31.rate.text);
    // Without the matrix: the server number.
    expect(
      successFrom(null, { run_id: "z", success_rate: { passed: 3, total: 4 } })
        .rate,
    ).toBe(0.75);
  });
});

describe("§C10: sentences are computed", () => {
  it("has no digit literals in its sentence templates", () => {
    const src = fs.readFileSync(
      path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "..",
        "leaderboardVersions.js",
      ),
      "utf8",
    );
    // Code only: comments quote example output.
    const code = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    const templates = code.match(/`[^`]*`/g) ?? [];
    expect(templates.length).toBeGreaterThan(3);
    const literalDigits = templates
      .map((t) => t.replace(/\$\{[^}]*\}/g, ""))
      .filter((t) => /\d/.test(t.replace(/\b(0|1|100)\b/g, "")));
    expect(literalDigits).toEqual([]);
  });

  it("perturbing one outcome changes the headline's numbers", () => {
    const before = versionHeadline(leaderboardNumbers(RUNS, MATRIX), RUNS);
    const m = clone(MATRIX);
    m.rows[3].cells.v31 = cell(1, 1);
    const after = versionHeadline(leaderboardNumbers(RUNS, m), RUNS);
    expect(after.text).not.toBe(before.text);
  });
});

describe("mergeMatrices", () => {
  it("merges cells of disjoint run reads by row", () => {
    const a = { rows: [{ id: "r1", cells: { x: [1] } }], columns: { x: {} } };
    const b = { rows: [{ id: "r1", cells: { y: [0] } }], columns: { y: {} } };
    const m = mergeMatrices([a, b]);
    expect(m.rows).toHaveLength(1);
    expect(Object.keys(m.rows[0].cells)).toEqual(["x", "y"]);
    expect(Object.keys(m.columns)).toEqual(["x", "y"]);
  });
});
