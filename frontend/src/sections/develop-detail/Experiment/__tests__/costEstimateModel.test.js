import { describe, expect, it } from "vitest";
import {
  aboutTime,
  carryOverLine,
  costLine,
  historyFromRuns,
  smokeSuggestion,
  targetKeyOfCard,
  targetKeyOfRun,
} from "../costEstimateModel";
import { experimentsOf } from "../StartFromExperiment";
import { reviewLine } from "../costEstimateModel";

const PROMPT = "3f000000-0000-4000-8000-000000000004";
const GRAPH = "6a000000-0000-4000-8000-000000000001";
const r1 = (letter, kind, config, cost) => ({
  run_id: `run-${letter}`,
  label: `#1${letter}`,
  letter,
  experiment_id: "exp-1",
  experiment_ordinal: 1,
  experiment_name: "Five ways to run support ops",
  dataset_version: { number: 1 },
  examples: 24,
  trials: 3,
  status: "Completed",
  kind,
  config: { kind, ...config },
  metrics: {
    aggregates: { "ops:cost": { avg: cost }, "ops:latency": { p50: 6000 } },
  },
});
const RUNS = [
  r1(
    "A",
    "prompt",
    { prompt: { id: PROMPT }, model: { id: "gpt-4o-mini" } },
    0.003999,
  ),
  r1("B", "agent", { agent: { graph_id: GRAPH } }, 0.011),
  r1("C", "code", { code: { mode: "sdk_preview" } }, 0.019),
  r1(
    "D",
    "harness",
    { harness: { name: "Claude Code", model: "claude-sonnet-4-5" } },
    0.21004,
  ),
  r1(
    "E",
    "harness",
    { harness: { name: "Codex CLI", model: "gpt-5-codex" } },
    0.12023,
  ),
];
const CARDS = [
  { targetType: "prompt", promptId: PROMPT, model: [{ value: "gpt-4o-mini" }] },
  { targetType: "agent", agentId: GRAPH },
  { targetType: "code" },
  {
    targetType: "harness",
    harness_config: { name: "Claude Code", model: "claude-sonnet-4-5" },
  },
  {
    targetType: "harness",
    harness_config: { name: "Codex CLI", model: "gpt-5-codex" },
  },
];

describe("costEstimateModel", () => {
  it("matches form cards to earlier runs of the same setup", () => {
    RUNS.forEach((run, i) => {
      if (run.kind === "code") return;
      const card = CARDS[i];
      expect(targetKeyOfCard(card, card.model?.[0]?.value)).toBe(
        targetKeyOfRun(run),
      );
    });
    expect(historyFromRuns(RUNS)).toHaveLength(5);
  });

  it("TS carry-over: 24 carried · 6 new rows × 5 runs × 3 trials = 90 attempts · ≈ $6.21", () => {
    const line = carryOverLine({
      source: {
        experimentId: "exp-1",
        label: "#1",
        versionNumber: 1,
        rows: 24,
      },
      versionNumber: 2,
      rows: 30,
      promptConfig: CARDS,
      trials: 3,
      history: historyFromRuns(RUNS),
    });
    expect(line.text).toBe(
      "Carry over 24 unchanged rows from #1 (same configs, same row text) · run 6 new rows × 5 runs × 3 trials = 90 attempts · ≈ $6.212390000000001 in Future AGI · runs without a priced model are not included.",
    );
    expect(
      carryOverLine({
        source: { experimentId: "exp-1", versionNumber: 2, rows: 24 },
        versionNumber: 2,
        rows: 30,
        promptConfig: CARDS,
        trials: 3,
      }),
    ).toBeNull();
  });

  it("words the cost line with calls, judges and time", () => {
    const line = costLine({
      promptConfig: [CARDS[1]],
      rows: 54,
      trials: 3,
      evals: [{ name: "Task success" }, { name: "Tone" }],
      history: historyFromRuns(RUNS),
    });
    // v2.1 (one estimator): an agent try is priced by the run engine (the
    // price the create stamps and the engine charges), not an earlier run;
    // its minutes are the engine's ETA (162 tries at 10 at once + scoring).
    expect(line.text).toBe(
      "162 agent calls (54 rows × 3 trials) · ≈ $2.3652 (rough) · judges 324 calls · ≈ $0.1944 · about 2 min",
    );
    expect(line.rough).toBe(true);
    const rough = costLine({
      promptConfig: [
        { targetType: "prompt", promptId: "p", model: [{ value: "gpt-4o" }] },
      ],
      rows: 10,
      trials: 1,
      evals: [],
      history: [],
    });
    expect(rough.text).toMatch(/≈ \$[\d.]+ \(rough\)/);
    const http = costLine({
      promptConfig: [{ targetType: "http" }],
      rows: 10,
      trials: 1,
      evals: [{ name: "Tone" }],
      history: [],
    });
    expect(http.text).toBe(
      "10 attempts (10 rows × 1 trial) · Cost depends on your endpoint · judges 10 calls · ≈ $0.006 · about 1 min",
    );
    expect(costLine({ promptConfig: [], rows: 10 })).toBeNull();
  });

  it("over $10 suggests the smoke split", () => {
    const inputs = {
      promptConfig: [CARDS[3]],
      trials: 3,
      evals: [],
      history: historyFromRuns(RUNS),
    };
    const line = costLine({ ...inputs, rows: 248 });
    expect(line.overBudget).toBe(true);
    const s = smokeSuggestion({
      ...inputs,
      rows: 248,
      splits: [
        { id: "t", name: "test", row_count: 40 },
        { id: "s", name: "smoke", row_count: 8 },
      ],
    });
    expect(s.text).toBe("Try the smoke split first: 8 rows · ≈ $5.04096");
    expect(smokeSuggestion({ ...inputs, rows: 5, splits: [] })).toBeNull();
  });

  it("lists experiments newest first with run counts", () => {
    const list = experimentsOf(RUNS);
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ ordinal: 1, rows: 24, trials: 3 });
    expect(list[0].runs.map((r) => r.letter)).toEqual([
      "A",
      "B",
      "C",
      "D",
      "E",
    ]);
    expect(aboutTime(10)).toBe("about 1 min"); // IT-R2 G1 P6: never seconds
    expect(aboutTime(65)).toBe("about 1 min");
  });
});

describe("reviewLine (R3 P3)", () => {
  it("says tasks × tries × runs = attempts with one cost and a time", () => {
    const line = reviewLine({
      promptConfig: [CARDS[1], CARDS[1]],
      rows: 30,
      trials: 3,
      evals: [],
      history: historyFromRuns(RUNS),
      taskSuite: true,
    });
    expect(line.text).toMatch(
      /^Run 30 tasks × 3 tries × 2 runs = 180 attempts · ≈ \$[\d.,]+( \(rough\))? · about \d+ (s|min)$/,
    );
    const rows = reviewLine({
      promptConfig: [CARDS[1]],
      rows: 1,
      trials: 1,
      evals: [],
      history: [],
    });
    expect(rows.text).toMatch(/^Run 1 row × 1 trial × 1 run = 1 attempt · /);
    expect(reviewLine({ promptConfig: [], rows: 10 })).toBeNull();
  });
});
