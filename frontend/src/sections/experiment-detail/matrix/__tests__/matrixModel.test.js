import { describe, expect, it } from "vitest";
import {
  agentShortName,
  autoPair,
  bootstrapCi,
  buildMatrixModel,
  columnStats,
  fmtDuration,
  fmtMoney,
  frontierSummary,
  normalizeSort,
  paretoModel,
  SHOW_OPTIONS,
  verdictInputs,
  versionsBanner,
} from "../matrixModel";
import { verdictOf } from "src/sections/common/ux/verdictModel";
import { TS2_MATRIX, runId } from "./ts2Matrix.fixture";

const A = runId("2a");
const B = runId("2b");
const C = runId("2c");
const D = runId("2d");
const E = runId("2e");
const RUNS = [A, B, C, D, E];

const runsById = {
  [A]: {
    run_id: A,
    label: "#2A",
    config: { prompt: { name: "Support Ops Reply", version: "V1" } },
  },
  [B]: {
    run_id: B,
    label: "#2B",
    config: { agent: { name: "Support Agent", version: "v3" } },
  },
  [C]: {
    run_id: C,
    label: "#2C",
    config: { code: { framework: "LangGraph 0.6" } },
  },
  [D]: {
    run_id: D,
    label: "#2D",
    config: { harness: { name: "Claude Code" } },
  },
  [E]: { run_id: E, label: "#2E", config: { harness: { name: "Codex CLI" } } },
};

describe("formatting", () => {
  it("formats money and durations through G0 fmt.js (R3 F1)", () => {
    // Trunk f225ecefc6: rounded cents; matrix still drops trailing zeros below a cent.
    expect(fmtMoney(0.004)).toBe("$0.004");
    expect(fmtMoney(0.0041)).toBe("$0.0041");
    expect(fmtMoney(0.011)).toBe("$0.011");
    expect(fmtMoney(0.2077)).toBe("$0.2077");
    expect(fmtMoney(null)).toBe("—");
    expect(fmtDuration(6100)).toBe("6.1 s");
    expect(fmtDuration(12400)).toBe("12 s");
    expect(fmtDuration(9830)).toBe("9.8 s");
    expect(fmtDuration(74000)).toBe("74 s");
    expect(fmtDuration(58000)).toBe("58 s");
    expect(fmtDuration(NaN)).toBe("—");
  });
});

describe("column blocks (§8.2, TS #2)", () => {
  const col = (id) => columnStats(TS2_MATRIX, id, B);
  it("pins pass@1, pass^3, $ / row and p50 / row", () => {
    expect(
      RUNS.map((id) => [
        col(id).passAt1Text,
        col(id).passHatKText,
        col(id).costText,
        col(id).p50Text,
      ]),
    ).toEqual([
      ["53.3% (48/90)", "43.3% (13/30)", "$0.004", "6.1 s"],
      ["65.6% (59/90)", "56.7% (17/30)", "$0.011000000000000003", "9.8 s"],
      ["85.6% (77/90)", "70.0% (21/30)", "$0.019000000000000003", "12 s"],
      ["91.0% (81/89)", "72.4% (21/29)", "$0.21000000000000002", "74 s"],
      ["83.3% (75/90)", "63.3% (19/30)", "$0.12000000000000005", "58 s"],
    ]);
  });
  it("counts rows better / worse than the baseline", () => {
    expect([A, C, D, E].map((id) => [col(id).better, col(id).worse])).toEqual([
      [6, 14],
      [10, 7],
      [10, 1],
      [11, 7],
    ]);
    expect(col(B).better).toBe(0);
  });
  it("names infra and errors", () => {
    expect(col(D).infraText).toBe("1 didn't run (setup) · not counted");
    expect(col(A).errorText).toBe("1 error · counted as a fail");
    expect(col(E).errorText).toBe("2 errors · counted as fails");
    expect(col(B).infraText).toBeNull();
  });
});

describe("buildMatrixModel", () => {
  const model = buildMatrixModel(TS2_MATRIX, { runIds: RUNS, baselineId: B });
  it("counts the Show sets", () => {
    expect(model.showCounts).toEqual({
      all: 30,
      disagree: 17,
      flaky: 16,
      nobody: 2,
      everyone: 7,
    });
    expect(model.headerText).toBe("30 rows · 5 runs · 3 trials each");
  });
  it("sorts by disagreement with the pinned order", () => {
    expect(model.rows.slice(0, 14).map((r) => r.label)).toEqual([
      "T20",
      "T1",
      "T2",
      "T8",
      "T11",
      "T17",
      "T26",
      "T29",
      "T4",
      "T12",
      "T25",
      "T6",
      "T27",
      "T30",
    ]);
  });
  it("filters to Disagree (17 rows) and names the callout rows", () => {
    const d = buildMatrixModel(TS2_MATRIX, {
      runIds: RUNS,
      baselineId: B,
      show: "disagree",
    });
    expect(d.rows).toHaveLength(17);
    expect(model.nobody.map((r) => r.label)).toEqual(["T19", "T10"]);
    expect(model.everyone.map((r) => r.label)).toEqual([
      "T3",
      "T5",
      "T7",
      "T9",
      "T15",
      "T16",
      "T22",
    ]);
    expect(model.anySolved).toBe(28);
  });
  it("solves T26 by 4 of 5", () => {
    expect(model.rows.find((r) => r.label === "T26").solvedText).toBe(
      "Solved by 4 of 5",
    );
  });
  it("groups by category with per-run pass@1", () => {
    const g = buildMatrixModel(TS2_MATRIX, {
      runIds: RUNS,
      baselineId: B,
      group: "category",
    });
    expect(g.groups.find((x) => x.key === "multi_request").header).toBe(
      "multi_request · 5 rows · 20% · 40% · 93% · 67% · 53%",
    );
    expect(g.groups.map((x) => x.key)).toEqual([
      "refund",
      "order_status",
      "address_change",
      "warranty_callback",
      "escalation",
      "multi_request",
    ]);
    expect(g.groups.find((x) => x.key === "refund").anySolved).toBe(5);
  });
  it("sorts by the other orders", () => {
    const by = (sort) =>
      buildMatrixModel(TS2_MATRIX, { runIds: RUNS, baselineId: B, sort })
        .rows[0].label;
    expect(by("row")).toBe("T1");
    expect(by("baseline")).toBe("T1");
    expect(normalizeSort("delta:eval:x:run:asc", RUNS)).toBe("disagreement");
    expect(normalizeSort(`run:${A}`, RUNS)).toBe(`run:${A}`);
  });
});

describe("autoPair", () => {
  const t26 = TS2_MATRIX.rows.find((r) => r.label === "T26");
  it("pairs #2B on T26 with the cheapest run that passed every trial", () => {
    expect(autoPair(t26, B, { baselineId: B, runIds: RUNS })).toBe(A);
  });
  it("pairs a candidate with the baseline when the baseline passed", () => {
    const t3 = TS2_MATRIX.rows.find((r) => r.label === "T3");
    expect(autoPair(t3, D, { baselineId: B, runIds: RUNS })).toBe(B);
  });
});

describe("paretoModel", () => {
  const p = paretoModel(TS2_MATRIX, { runIds: RUNS, runsById });
  it("marks #2E dominated by #2C and the others on the frontier", () => {
    const e = p.points.find((x) => x.id === E);
    expect(e.frontier).toBe(false);
    expect(e.tooltip).toBe("Dominated by #2C: cheaper and better");
    expect(p.frontier.map((x) => x.label)).toEqual([
      "#2A",
      "#2B",
      "#2C",
      "#2D",
    ]);
  });
  // L7c g6 (chart:pareto-matrix): the gap is rounded once from the
  // unrounded rates (runNumbers.ptsDelta), the delta every verdict prints.
  it("writes the sentence with the one pts delta", () => {
    expect(p.sentence).toBe(
      "Claude Code passes 5.5 pts more trials than #2C for 11× the cost per task.",
    );
  });
  it("bootstraps a deterministic CI around pass@1", () => {
    const d = p.points.find((x) => x.id === D);
    expect(d.ci.low).toBeLessThan(d.quality);
    expect(d.ci.high).toBeGreaterThan(d.quality);
    const again = paretoModel(TS2_MATRIX, { runIds: RUNS, runsById });
    expect(again.points.find((x) => x.id === D).ci).toEqual(d.ci);
    expect(bootstrapCi([])).toBeNull();
  });
  it("lets an iteration on the frontier speak", () => {
    const G = "g-run";
    const m = {
      ...TS2_MATRIX,
      columns: { ...TS2_MATRIX.columns, [G]: { label: "#2G", trials: 3 } },
      rows: TS2_MATRIX.rows.map((r) => ({
        ...r,
        cells: {
          ...r.cells,
          // v3.1: #2B's costs; passes = #2C's minus one attempt (76/90).
          [G]: {
            ...r.cells[B],
            trials:
              r.label === "T28"
                ? [0, 0, 1]
                : r.cells[C].trials.map((t) => (t === 1 ? 1 : 0)),
          },
        },
      })),
    };
    const s = paretoModel(m, {
      runIds: [...RUNS, G],
      runsById: {
        ...runsById,
        [G]: {
          run_id: G,
          label: "#2G",
          parent_run_id: B,
          iteration: { n: 2 },
          config: { agent: { name: "Support Agent", version: "v3.1 (draft)" } },
        },
      },
    });
    expect(s.sentence).toMatch(
      /^v3\.1 sits on the frontier: [\d.]+ pts below your LangGraph agent at \d+% of its cost\.$/,
    );
  });
});

describe("versionsBanner", () => {
  it("names the versions when runs differ", () => {
    expect(versionsBanner(["a", "b"], (id) => (id === "a" ? 1 : 2)).text).toBe(
      'Runs used different versions (v1, v2). Rows only in v2 show as "not run" for v1 runs.',
    );
    expect(versionsBanner(["a", "b"], () => 2)).toBeNull();
  });
});

describe("R3: chips, frontier strip, verdict inputs", () => {
  it("names the Show chips with the F3 words", () => {
    expect(SHOW_OPTIONS.map((o) => o.label)).toEqual([
      "All",
      "Pass/fail differs",
      "Flaky",
      "No run reliable",
      "All reliable",
    ]);
  });
  it("summarises the frontier: #2D most solved, #2C best value", () => {
    const p = paretoModel(TS2_MATRIX, { runIds: RUNS, runsById });
    expect(frontierSummary(p).text).toBe(
      "Frontier: #2D most solved · #2C best value",
    );
  });
  it("feeds verdictOf the pinned #2D sentence", () => {
    const model = buildMatrixModel(TS2_MATRIX, { runIds: RUNS, baselineId: B });
    const items = verdictInputs(model, runsById);
    expect(items.map((i) => i.label)).toEqual(["#2A", "#2C", "#2D", "#2E"]);
    const d = items.find((i) => i.runId === D);
    expect(verdictOf(d.input).sentence).toBe(
      "#2D Claude Code beats baseline #2B: +25.4 pts success · improved 10 · worse 1 · 19× cost · 64 s slower. Confidence: medium (30 tasks × 3 tries).",
    );
    expect(items.find((i) => i.runId === C).worseRows).toHaveLength(7);
  });
});

describe("agentShortName (it-r3 finalize)", () => {
  it("names an R4 code run by its name, never the bare kind", () => {
    const run = {
      name: "CrewAI support crew",
      kind: "code",
      config: {
        kind: "code",
        code: { repo: "acme/crew" },
        framework: { name: "CrewAI" },
      },
    };
    expect(agentShortName(run)).toBe("CrewAI support crew");
  });
  it("keeps `your <framework> agent` and the unnamed fallback", () => {
    expect(
      agentShortName({ config: { code: { framework: "LangGraph 0.2" } } }),
    ).toBe("your LangGraph agent");
    expect(agentShortName({ config: { code: {} } })).toBe("your code");
  });
});
