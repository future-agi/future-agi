import { describe, expect, it } from "vitest";
import {
  buildIterateBody,
  canIterate,
  changeTabsFor,
  collectChanges,
  fmtAbout,
  focusRunOf,
  fmtUsd,
  KEEP_PREFIX_TEXT,
  landsAs,
  nextAgentVersion,
  presetOf,
  previewLine,
  previewOf,
  rowMatches,
  scopeOptions,
  scopeSets,
  suggestionsFor,
} from "../iterateModel";
import {
  bannerSentence,
  costChip,
  hopDelta,
  pass1Chip,
  passKChip,
  pText,
  signSentence,
  summarizeIteration,
} from "../iterateSummary";
import {
  chainCompare,
  hopLabel,
  iterationGroupOf,
  joinLineage,
  liftIterationsOf,
  lineageBranch,
  nestIterations,
  nestSorted,
} from "../lineageModel";
import { quickTestsOf } from "../useQuickTests";

// TS #2B (STORY.md §8.2) and the scripted iterations.
const B = {
  1: "000",
  2: "000",
  3: "111",
  4: "111",
  5: "111",
  6: "111",
  7: "111",
  8: "111",
  9: "111",
  10: "000",
  11: "000",
  12: "111",
  13: "111",
  14: "111",
  15: "111",
  16: "111",
  17: "000",
  18: "101",
  19: "010",
  20: "000",
  21: "110",
  22: "111",
  23: "010",
  24: "111",
  25: "111",
  26: "000",
  27: "111",
  28: "011",
  29: "000",
  30: "111",
};
const PREFIX = [1, 2, 11, 17, 26, 29];
const G = { ...B, 13: "110", 23: "011", 28: "001" };
PREFIX.forEach((n) => {
  G[n] = "111";
});
const vec = (s) => s.split("").map(Number);
const id = (n) => `row-${n}`;
const cell = (s, extra = {}) => ({
  trials: vec(s),
  cost_usd: [0.011, 0.011, 0.011],
  ...extra,
});
const matrixOf = (runs) => ({
  rows: Object.keys(B).map((k) => {
    const n = Number(k);
    const cells = {};
    Object.entries(runs).forEach(([runId, fn]) => {
      const c = fn(n);
      if (c) cells[runId] = c;
    });
    return { row_id: id(n), n, label: `T${n}`, cells };
  }),
});
const M = matrixOf({
  B: (n) => cell(B[n]),
  F: (n) =>
    PREFIX.includes(n) ? cell("111") : cell(B[n], { carried_from: "#2B" }),
  G: (n) => cell(G[n]),
});

describe("formatting", () => {
  it("money, durations", () => {
    expect(fmtUsd(0.198)).toBe("$0.198");
    expect(fmtUsd(0.792)).toBe("$0.792");
    expect(fmtUsd(0.011)).toBe("$0.011");
    expect(fmtUsd(null)).toBe("—");
    expect(fmtAbout(10)).toBe("about 10 s");
    expect(fmtAbout(90)).toBe("about 2 min");
  });
});

describe("scopes", () => {
  it("counts every scope on #2B", () => {
    const s = scopeSets(M, "B", {
      baselineId: "A",
      rows: ["T1", "T2", "T11", "T17", "T26", "T29"],
      filterRowIds: [id(10), id(20)],
      rowId: "T7",
    });
    expect(s.all).toHaveLength(30);
    expect(s.failed).toHaveLength(13);
    expect(s.flaky).toHaveLength(5);
    expect(s.regressed).toEqual([]); // no cells for A
    expect(s.rows).toEqual(PREFIX.map(id));
    expect(s.filter).toEqual([id(10), id(20)]);
    expect(s.row).toEqual([id(7)]);
  });
  it("leaves out options that do not apply", () => {
    const s = scopeSets(M, "B", {});
    expect(s.regressed).toBeNull();
    expect(s.rows).toBeNull();
    expect(s.filter).toBeNull();
    const opts = scopeOptions(s, { parentLabel: "#2B" });
    expect(opts.map((o) => o.label)).toEqual([
      "All (30)",
      "Failed (13)",
      "Flaky (5)",
      // iterate r2 G3 (P4): fix the failing rows, then check the rest.
      "Failing rows first, then the other 17 (30)",
    ]);
    // iterate r3 G3 (P2): the Failed chip breaks its number down.
    expect(opts[1].tip).toBe(
      "13 rows have a failing try on #2B; 8 fail every try",
    );
    expect(opts[3].count).toBe(30);
  });
  it("a finding's rows read `Rows failing order_found (6)` (P3)", () => {
    const s = scopeSets(M, "B", { rows: PREFIX.map(id) });
    const opts = scopeOptions(s, {
      parentLabel: "#2B",
      source: "Finding",
      causeLabel: "order_found",
    });
    expect(opts.find((o) => o.type === "rows").label).toBe(
      "Rows failing order_found (6)",
    );
    const picked = scopeOptions(scopeSets(M, "B", { rows: [id(7)] }), {
      parentLabel: "#2B",
    });
    expect(picked.find((o) => o.type === "rows").label).toBe("1 selected row");
  });
  it("regressed = rows that pass less often than on the baseline", () => {
    const s = scopeSets(M, "G", { baselineId: "B" });
    expect(s.regressed).toEqual([id(13), id(28)]);
  });
  it("row refs: id, T7, 7, Row 7", () => {
    const row = { row_id: "x", n: 7, label: "T7" };
    ["x", "T7", "7", "Row 7"].forEach((r) =>
      expect(rowMatches(row, r)).toBe(true),
    );
    expect(rowMatches(row, "T70")).toBe(false);
  });
});

describe("presets, tabs, changes", () => {
  const agentRun = {
    run_id: "B",
    config: {
      kind: "agent",
      agent: {
        version: "v3",
        nodes: [
          {
            id: "classify_intent",
            type: "llm",
            prompt: "Extract the order number as digits only.",
          },
          { id: "lookup_order", type: "tool" },
        ],
        limits: { max_iterations: 12 },
      },
      tools: [{ name: "lookup_order", mode: "live" }],
    },
  };
  it("preset ids and chips", () => {
    expect(presetOf("RC_PREFIX").node).toBe("classify_intent");
    expect(presetOf(null, "RC_PREFIX:Keep").id).toBe("RC_PREFIX");
    expect(presetOf("tighten_prompt")).toBeNull();
    expect(
      suggestionsFor(
        "classify_intent",
        "Extract the order number as digits only.",
      ).map((c) => c.label),
    ).toEqual(["Keep the prefix", "Revert to v2"]);
    expect(suggestionsFor("respond", "x")).toEqual([]);
  });
  it("tabs by kind", () => {
    expect(changeTabsFor(agentRun)).toEqual(["agent", "tools"]);
    expect(
      changeTabsFor({ config: { kind: "prompt", tools: [{ name: "a" }] } }),
    ).toEqual(["model", "tools"]);
    // dataset integration FE-0 (review must-fix 1): a run Future AGI cannot start
    // again itself gets no tab (no Code tab printing a generated runner),
    // and an endpoint run has nothing to edit here.
    ["harness", "code", "voice", "upload", "traces", "http"].forEach((kind) =>
      expect(changeTabsFor({ config: { kind } }), kind).toEqual([]),
    );
    expect(canIterate({ config: { kind: "prompt" } })).toBe(true);
    expect(canIterate(agentRun)).toBe(true);
    expect(canIterate({ config: { kind: "code" } })).toBe(false);
  });
  it("collects only what changed", () => {
    expect(
      collectChanges(agentRun.config, {
        agent: {
          node: "classify_intent",
          prompt: "Extract the order number as digits only.",
          maxIterations: 12,
        },
      }),
    ).toEqual([]);
    expect(
      collectChanges(agentRun.config, {
        agent: {
          node: "classify_intent",
          prompt: KEEP_PREFIX_TEXT,
          maxIterations: 16,
        },
        tools: { modes: { lookup_order: "mock" } },
      }),
    ).toEqual([
      {
        axis: "agent",
        node: "classify_intent",
        field: "prompt",
        value: KEEP_PREFIX_TEXT,
      },
      { axis: "agent", field: "max_iterations", value: 16 },
      { axis: "tools", node: "lookup_order", field: "mode", value: "mock" },
    ]);
    expect(
      collectChanges(
        { model: { id: "gpt-4o-mini", params: { temperature: 0.2 } } },
        { model: { id: "gpt-4o", temperature: 0.2 } },
      ),
    ).toEqual([{ axis: "model", field: "id", value: "gpt-4o" }]);
    expect(nextAgentVersion("v3")).toBe("v3.1");
    expect(nextAgentVersion("v3.1")).toBe("v3.2");
  });
  it("builds the I1 body (spec §4.5)", () => {
    const body = buildIterateBody({
      parentRunId: "B",
      scopeType: "rows",
      rowIds: PREFIX.map(id),
      scopeLabel: "Finding: 6 rows",
      trials: 3,
      carryOver: true,
      changes: [
        {
          axis: "agent",
          node: "classify_intent",
          field: "prompt",
          value: KEEP_PREFIX_TEXT,
        },
      ],
      saveAs: { agent_version_name: "v3.1", status: "draft" },
      causeId: "RC_PREFIX",
    });
    expect(body).toMatchObject({
      parent_run_id: "B",
      kind: "iteration",
      scope: {
        type: "rows",
        row_ids: PREFIX.map(id),
        label: "Finding: 6 rows",
      },
      trials: 3,
      carry_over: true,
      save_as: { agent_version_name: "v3.1" },
      cause_id: "RC_PREFIX",
    });
    const quick = buildIterateBody({
      parentRunId: "B",
      quickTest: true,
      scopeType: "failed",
      trials: 3,
    });
    expect(quick).toMatchObject({
      kind: "quick_test",
      trials: 1,
      carry_over: false,
      scope: { type: "failed" },
    });
    expect(quick.scope.row_ids).toBeUndefined();
    expect(quick.save_as).toBeUndefined();
  });
});

describe("preview and lands-as", () => {
  it("6 rows × 3 trials at $0.011 → ≈ $0.20 · about 10 s", () => {
    const p = previewOf({
      rows: 6,
      trials: 3,
      kind: "agent",
      costPerAttemptUsd: 0.011,
    });
    expect(previewLine(p, "agent")).toBe(
      "6 rows × 3 trials = 18 attempts · ≈ $0.198 · about 10 s",
    );
    const g = previewOf({
      rows: 24,
      trials: 3,
      kind: "agent",
      costPerAttemptUsd: 0.011,
    });
    expect(previewLine(g, "agent")).toBe(
      "24 rows × 3 trials = 72 attempts · ≈ $0.792 · about 15 s",
    );
    expect(previewLine(p, "code")).toBe("6 rows × 3 trials = 18 attempts");
  });
  const exp = (n, extra = {}) =>
    Array.from({ length: n }, (_, i) => ({
      run_id: `r${i}`,
      experiment_id: "E2",
      experiment_name: "Run sources bake-off v2",
      experiment_ordinal: 2,
      letter: String.fromCharCode(65 + i),
      ...extra,
    }));
  it("next letter in the experiment", () => {
    const runs = exp(5);
    expect(landsAs({ parent: runs[1], datasetRuns: runs }).sentence).toBe(
      'Lands as #2F in "Run sources bake-off v2", compared with #2B.',
    );
  });
  it("at 12 runs, a new experiment", () => {
    const runs = [
      ...exp(12),
      { run_id: "x", experiment_id: "E1", experiment_ordinal: 1, letter: "A" },
    ];
    expect(landsAs({ parent: runs[1], datasetRuns: runs }).sentence).toBe(
      'This experiment has 12 runs. Lands as #3A in a new experiment "Run sources bake-off v2 · iterations", still compared with #2B.',
    );
  });
});

describe("banner summary", () => {
  it("#2F: 6 fixed, significant", () => {
    const s = summarizeIteration(M, "B", "F");
    expect(
      bannerSentence(s, { candLabel: "#2F", baseLabel: "#2B", partial: true }),
    ).toBe(
      "#2F reran 6 rows: 6 fixed, 0 still failing, 0 new failures. 24 rows carried over from #2B.",
    );
    expect(pass1Chip(s)).toBe("pass@1 65.6% → 85.6% (+20.0 pts, significant)");
    expect(passKChip(s)).toBe("pass^3 56.7% → 76.7%");
    expect(costChip(s)).toBe("cost / row unchanged $0.011000000000000008");
    expect(pText(s.sign.p)).toBe("p = 0.031");
    expect(signSentence(s)).toBe(
      "6 rows got better and none got worse. If the change did nothing, a split this lopsided happens 3% of the time (sign test).",
    );
    expect(hopDelta(s)).toBe("+20.0 pts on 6 rows");
  });
  it("#2G: 7 better, 2 worse, within noise", () => {
    const s = summarizeIteration(M, "B", "G");
    expect(
      bannerSentence(s, { candLabel: "#2G", baseLabel: "#2B", partial: false }),
    ).toBe(
      "#2G ran all 30 rows: 7 better, 2 worse (1 trial each, within noise), 21 same.",
    );
    expect(pass1Chip(s)).toBe("pass@1 65.6% → 84.4% (+18.8 pts, within noise)");
    expect(passKChip(s)).toBe("pass^3 56.7% → 73.3%");
    expect(pText(s.sign.p)).toBe("p = 0.18");
  });
});

describe("lineage", () => {
  const cfg = (prompt, version) => ({
    kind: "agent",
    agent: {
      name: "Support Agent",
      version,
      status: "draft",
      nodes: [{ id: "classify_intent", type: "llm", prompt }],
    },
  });
  const runs = [
    {
      run_id: "B",
      label: "#2B",
      created_at: "1",
      config: cfg("digits only", "v3"),
    },
    {
      run_id: "F",
      label: "#2F",
      created_at: "2",
      parent_run_id: "B",
      iteration: { root_run_id: "B", n: 1, rows_rerun: 6, rows_carried: 24 },
      config: cfg(KEEP_PREFIX_TEXT, "v3.1"),
    },
    {
      run_id: "G",
      label: "#2G",
      created_at: "3",
      parent_run_id: "F",
      iteration: { root_run_id: "B", n: 2, rows_rerun: 30, rows_carried: 0 },
      config: cfg(KEEP_PREFIX_TEXT, "v3.1"),
    },
    {
      run_id: "H",
      label: "#2H",
      created_at: "4",
      parent_run_id: "B",
      iteration: { root_run_id: "B", n: 3, rows_rerun: 30, rows_carried: 0 },
      config: cfg("x", "v3.2"),
    },
    { run_id: "A", label: "#2A", created_at: "0" },
  ];
  it("branch on view, other branches counted", () => {
    const b = lineageBranch(runs, ["B", "F"]);
    expect(b.path).toEqual(["B", "F", "G"]);
    expect(b.otherBranches).toEqual([["B", "H"]]);
    expect(lineageBranch(runs, ["A", "B", "F"]).path).toEqual(["B", "F", "G"]);
    expect(lineageBranch(runs, ["A"]).rootId).toBe("A");
    expect(lineageBranch(runs, ["A"]).path).toEqual(["A"]);
  });
  it("hop labels ≤ 32 chars", () => {
    // R4 8e: named from the change (the chip whose text the prompt holds).
    expect(hopLabel(runs[0], runs[1])).toBe("Keep the prefix · 6 rows");
    expect(hopLabel(runs[1], runs[2])).toBe("all 30 rows");
    const long = hopLabel(
      { config: { model: { id: "gpt-4o-mini" } } },
      {
        config: { model: { id: "claude-sonnet-4-5-very-long-name" } },
        iteration: { rows_rerun: 4, rows_carried: 26 },
      },
    );
    expect(long.length).toBeLessThanOrEqual(32);
    expect(long.endsWith("· 4 rows")).toBe(true);
  });
  it("chain compare caps at 8", () => {
    const path = Array.from({ length: 11 }, (_, i) => `r${i}`);
    expect(chainCompare(path)).toMatchObject({
      runIds: path.slice(3),
      baselineId: "r3",
      capped: true,
      total: 11,
    });
  });
  it("leaderboard nesting under the root, collapsed by default", () => {
    const lb = joinLineage(
      [
        { run_id: "A" },
        { run_id: "B" },
        { run_id: "F" },
        { run_id: "G" },
        { run_id: "H" },
      ],
      runs,
    );
    const closed = nestIterations(lb);
    expect(closed.map((r) => r.run_id)).toEqual(["A", "B"]);
    expect(closed[1]).toMatchObject({ __iterations: 3 });
    expect(closed[1].__latest.run_id).toBe("H");
    const open = nestIterations(lb, new Set(["B"]));
    expect(open.map((r) => `${r.run_id}${r.__depth}`)).toEqual([
      "A0",
      "B0",
      "F1",
      "G1",
      "H1",
    ]);
    expect(
      iterationGroupOf(lb[2], [
        ...lb,
        ...runs.map((r) => ({ ...r, __root: null })),
      ]).key,
    ).toBe("root:B");
  });
});

describe("focusRunOf", () => {
  const runs = [
    { run_id: "A" },
    { run_id: "B", is_dataset_baseline: true },
    { run_id: "C" },
  ];
  it("prefers cand, then the URL baseline, then the dataset baseline", () => {
    expect(
      focusRunOf({ runIds: ["A", "B", "C"], runs, cand: "C", baselineId: "A" }),
    ).toBe("C");
    expect(
      focusRunOf({
        runIds: ["A", "B", "C"],
        runs,
        urlBaseline: "C",
        baselineId: "A",
      }),
    ).toBe("C");
    expect(focusRunOf({ runIds: ["A", "B", "C"], runs, baselineId: "A" })).toBe(
      "B",
    );
  });
  it("falls back to the page baseline, then the first run, ignoring runs not in view", () => {
    expect(focusRunOf({ runIds: ["A", "C"], runs, baselineId: "C" })).toBe("C");
    expect(focusRunOf({ runIds: ["A", "C"], runs, cand: "Z" })).toBe("A");
    expect(focusRunOf({ runIds: [], runs: [] })).toBe(null);
  });
});

describe("nestSorted", () => {
  const B = { run_id: "B", __depth: 0 };
  const F = {
    run_id: "F",
    __depth: 1,
    __root: "B",
    created_at: "2026-10-15T01:00:00Z",
  };
  const G = {
    run_id: "G",
    __depth: 1,
    __root: "B",
    created_at: "2026-10-15T02:00:00Z",
  };
  const A = { run_id: "A", __depth: 0 };
  it("puts iterations under their root, oldest first, after any sort", () => {
    expect(nestSorted([G, A, F, B]).map((r) => r.run_id)).toEqual([
      "A",
      "B",
      "F",
      "G",
    ]);
    const nodes = [G, B, A, F].map((data) => ({ data }));
    expect(nestSorted(nodes, (n) => n.data).map((n) => n.data.run_id)).toEqual([
      "B",
      "F",
      "G",
      "A",
    ]);
  });
  it("keeps an iteration in place when its root is not in the list", () => {
    expect(nestSorted([G, A]).map((r) => r.run_id)).toEqual(["G", "A"]);
  });
});

describe("liftIterationsOf", () => {
  const B = { run_id: "B", __depth: 0, __root: null };
  const F = { run_id: "F", __depth: 1, __root: "B" };
  const G = { run_id: "G", __depth: 1, __root: "B" };
  const Y = { run_id: "Y", __depth: 1, __root: "X" };
  it("lists the hidden root's iterations as plain runs (pinned baseline)", () => {
    const lifted = liftIterationsOf([B, F, G, Y], "B");
    expect(lifted.map((r) => `${r.run_id}${r.__depth}`)).toEqual([
      "B0",
      "F0",
      "G0",
      "Y1",
    ]);
    expect(lifted[1].__root).toBe("B");
    // Nothing nests under the hidden root any more: no toggle, no fold.
    const nested = nestIterations(lifted, { has: () => true });
    expect(nested.find((r) => r.run_id === "B").__iterations).toBeUndefined();
    expect(nested.map((r) => r.run_id)).toEqual(["B", "F", "G", "Y"]);
  });
  it("returns the runs as they are without a hidden root", () => {
    const runs = [B, F];
    expect(liftIterationsOf(runs, null)).toBe(runs);
    expect(liftIterationsOf(null, "B")).toEqual([]);
  });
});

describe("quickTestsOf", () => {
  it("keeps unlisted quick tests only", () => {
    const runs = [
      { run_id: "a", listed: true, kind: "agent" },
      {
        run_id: "q",
        listed: false,
        kind: "agent",
        source: { kind: "quick_test" },
      },
      {
        run_id: "r",
        listed: false,
        kind: "replay",
        source: { kind: "replay" },
      },
    ];
    expect(quickTestsOf(runs).map((r) => r.run_id)).toEqual(["q"]);
    expect(quickTestsOf(null)).toEqual([]);
  });
});

describe("W1-1 · later runs of a config nest as runs, not iterations", () => {
  it("words kept runs as runs and changed setups as iterations", async () => {
    const { nestIterations, nestWordsOf, iterationGroupOf } = await import(
      "../lineageModel"
    );
    const root = {
      run_id: "a",
      experiment_id: "e",
      letter: "A",
      label: "#2A",
      created_at: "1",
    };
    const kept = (id, attempt, at) => ({
      run_id: id,
      experiment_id: "e",
      letter: "A",
      attempt,
      label: `#2A·${attempt}`,
      __depth: 1,
      __root: "a",
      created_at: at,
    });
    const rows = nestIterations([root, kept("b", 2, "2"), kept("c", 3, "3")]);
    expect(rows[0].__kept).toBe(2);
    expect(nestWordsOf(rows[0])).toEqual({
      count: "2 more runs",
      latest: "latest run",
    });
    const fix = { ...kept("d", 1, "4"), letter: "G", label: "#2G" };
    const mixed = nestIterations([root, kept("b", 2, "2"), fix]);
    expect(nestWordsOf(mixed[0])).toEqual({
      count: "2 iterations",
      latest: "latest iteration",
    });
    expect(
      iterationGroupOf(kept("b", 2, "2"), [root, kept("b", 2, "2")]).label,
    ).toBe("#2A · 1 more run");
  });
});
