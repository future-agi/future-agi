// IT-R1 G1 P6: estimates and limits from one source (each run's Limits).
import { describe, expect, it } from "vitest";
import { runEstimateLines } from "../costEstimateModel";

const cards = [
  {
    targetType: "harness",
    name: "Claude Code",
    runName: "Claude Code",
    r4: { mode: "local", entryId: "claude_code", limits: { concurrency: 2 } },
  },
  {
    targetType: "code",
    name: "support_agent · Custom TypeScript",
    runName: "support_agent · Custom TypeScript",
    r4: {
      mode: "local",
      entryId: "custom_typescript",
      limits: { concurrency: 4, maxCostUsd: 5 },
    },
  },
];

describe("runEstimateLines (IT-R1 G1 P6)", () => {
  it("one line per run with its cap and concurrency; one headline", () => {
    const out = runEstimateLines({
      cards,
      rows: 30,
      trials: 1,
      ordinal: 4,
      estimate: {
        judge_cost_usd: 0.11,
        per_target: [{ cost_usd: 3.1 }, { cost_usd: 0 }],
      },
    });
    // IT-R2 G1 P6: expected (from history) vs worst case (caps), one time.
    expect(out.lines[0].text).toBe(
      "#4A Claude Code · 30 tries · expected ≈ $3.10 · stops at $1.00 per try · 2 at a time · ~15–23 min",
    );
    expect(out.lines[1].text).toMatch(
      /^#4B .* · 30 tries · ~\$ — add your model to estimate · stops at \$0\.16666666666666666 per try · \$5\.00 per run of 30 · 4 at a time/,
    );
    expect(out.judges).toBe("Future AGI judges ≈ $0.11");
    expect(out.headline).toBe(
      "Expected ≈ $3.21 = $3.10 runs + $0.11 judges · worst case $35.11 (caps) · about 15–23 min",
    );
    // IT-R5 G1 P4: the worst case shows its arithmetic; one chip.
    expect(out.worstMath).toBe(
      "Worst case $35.11 = 30 tries × $1.00 + $5.00 + $0.11 judges",
    );
    expect(out.chip).toBe("2 runs · ~$3.21 · ~15–23 min");
  });
});

describe("runEstimateLines (IT-R2 G1 P6) · gaps and voice", () => {
  it("explains a big gap between two harness runs from their last runs", () => {
    const two = [
      {
        targetType: "harness",
        name: "Claude Code",
        harness_config: { name: "Claude Code", model: "claude-sonnet-4-5" },
        r4: { mode: "local", entryId: "claude_code", entryName: "Claude Code" },
      },
      {
        targetType: "harness",
        name: "Codex CLI",
        harness_config: { name: "Codex CLI", model: "gpt-5-codex" },
        r4: { mode: "local", entryId: "codex_cli", entryName: "Codex CLI" },
      },
    ];
    const out = runEstimateLines({
      cards: two,
      rows: 30,
      trials: 1,
      ordinal: 4,
      estimate: { per_target: [{ cost_usd: 6.4 }, { cost_usd: 3.6 }] },
      history: [
        {
          target_key: "harness|Claude Code|claude-sonnet-4-5",
          label: "#2D",
          tokens_per_attempt: 30000,
        },
        {
          target_key: "harness|Codex CLI|gpt-5-codex",
          label: "#2E",
          tokens_per_attempt: 20000,
        },
      ],
    });
    expect(out.gaps).toEqual([
      "Claude Code costs ~1.8× Codex CLI here: ~1.5× the tokens per task (from #2D vs #2E)",
    ]);
  });
});

describe("IT-R4 G1 P5 · a declared model is priced, else not measured", () => {
  it("prices Your code at the declared model's list prices", async () => {
    const { runsCompareTable } = await import("../reviewModel");
    const code = {
      targetType: "code",
      name: "support_agent · LangGraph",
      runName: "support_agent · LangGraph",
      r4: {
        kind: "code",
        mode: "local",
        entryId: "langgraph",
        entryName: "LangGraph",
        declared: { model: "gpt-4.1", tools: ["lookup_order"], mcp: [] },
      },
    };
    const out = runEstimateLines({
      cards: [code],
      rows: 24,
      trials: 3,
      ordinal: 2,
      estimate: { per_target: [{ cost_usd: 0 }] },
    });
    expect(out.lines[0].text).toContain("≈ $2.1024 at gpt-4.1 prices");
    const table = runsCompareTable(
      [code],
      [{ configIndex: 0, letter: "A", targetType: "code", name: "x" }],
      { ordinal: 2 },
    );
    expect(table.rows?.[0]?.model ?? table[0]?.model).toBe("gpt-4.1");
  });
});

describe("options-truth L2 finalize · a harness run's own cost cap", () => {
  it("expected and worst case stop at Stop if cost goes over", () => {
    const harness = (limits) => [
      {
        targetType: "harness",
        name: "Claude Code",
        runName: "Claude Code",
        r4: { mode: "local", entryId: "claude_code", limits },
      },
    ];
    const est = { judge_cost_usd: 0.036, per_target: [{ cost_usd: 6.3 }] };
    const free = runEstimateLines({
      cards: harness({ concurrency: 2 }),
      rows: 30,
      trials: 1,
      ordinal: 4,
      estimate: est,
    });
    // Worst cases carry the judges ($0.036) once.
    expect(free.worst).toBeCloseTo(30.036, 6);
    const capped = runEstimateLines({
      cards: harness({ concurrency: 2, maxCostUsd: 5 }),
      rows: 30,
      trials: 1,
      ordinal: 4,
      estimate: est,
    });
    expect(capped.lines[0].text).toBe(
      "#4A Claude Code · 30 tries · expected ≈ $5 · stops at $1.00 per try · $5.00 per run · 2 at a time · ~15–23 min",
    );
    expect(capped.lines[0]).toMatchObject({ clamped: true, runCap: 5 });
    expect(capped.worst).toBeCloseTo(5.036, 6);
    expect(capped.expected).toBeCloseTo(5.036, 6);
    expect(capped.headline).toMatch(
      /^Expected ≈ \$5\.036 = \$5\.00 runs \+ \$0\.036 judges · worst case \$5\.036 \(caps\)/,
    );
    // A cap above every try's cap changes nothing.
    const loose = runEstimateLines({
      cards: harness({ concurrency: 2, maxCostUsd: 50 }),
      rows: 30,
      trials: 1,
      ordinal: 4,
      estimate: est,
    });
    expect(loose.lines[0].text).toBe(free.lines[0].text);
    expect(loose.worst).toBeCloseTo(free.worst, 6);
  });
});
