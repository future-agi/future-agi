// options-truth L3 G3: one estimator for every surface (R1) and Review's
// per-run tries and at a time (R2).
import { describe, expect, it } from "vitest";
import {
  judgeCallUsd,
  minutesFor,
  ownTriesLine,
  ownTriesOf,
  replayOf,
  resolvedAtOnce,
  runTriesOf,
  trySecondsAt,
} from "../estimateInputs";
import {
  ESTIMATE_HELP,
  carryOverLine,
  costLine,
  judgesOf,
  runEstimateLines,
} from "../costEstimateModel";
import { flattenRuns } from "../runPlan";
import { JUDGE_COST_PER_CALL } from "src/sections/common/Analysis/runStats";
import {
  MODEL_LATENCY_MS,
  costPerTry,
} from "src/sections/common/RunSources/declaredUses";

const code = (model, limits = {}) => ({
  targetType: "code",
  name: "support_agent · Custom Python",
  runName: "support_agent · Custom Python",
  r4: {
    kind: "code",
    mode: "local",
    entryId: "custom_python",
    entryName: "Custom Python",
    declared: { model },
    limits,
  },
});
const prompt = (model) => ({
  targetType: "prompt",
  promptId: "p1",
  name: "Support Ops Reply",
  model: [{ value: model }],
});

describe("estimateInputs", () => {
  it("a card's own tries win; prompt runs take the experiment's Trials", () => {
    const a = code("gpt-4.1", { tries: 3 });
    const b = code("gpt-4.1", { tries: 2 });
    const voice = {
      targetType: "voice",
      r4: { kind: "voice", slot: { voice: { tries: 4 } } },
    };
    expect(ownTriesOf(a)).toBe(3);
    expect(ownTriesOf(voice)).toBe(4);
    expect(ownTriesOf(prompt("gpt-4o"))).toBeNull();
    expect(runTriesOf(b, 1)).toBe(2);
    expect(runTriesOf(prompt("gpt-4o"), 5)).toBe(5);
    const cards = [a, b, prompt("gpt-4o")];
    expect(ownTriesLine(cards, flattenRuns(cards), { ordinal: 9 })).toBe(
      "Tries per run: #9A 3 · #9B 2",
    );
    expect(ownTriesLine([prompt("x")], flattenRuns([prompt("x")]))).toBeNull();
  });

  it("at a time = min(card, experiment At most, sandbox) with its reason", () => {
    const c = code("gpt-4.1", { concurrency: 4 });
    expect(resolvedAtOnce(c, {})).toEqual({ value: 4, reason: null });
    const one = resolvedAtOnce(c, { maxConcurrency: 1 });
    expect(one.value).toBe(1);
    expect(one.reason).toBe("the experiment runs at most 1 at once; you set 4");
    expect(resolvedAtOnce(c, { maxConcurrency: 10, sandboxMax: 2 }).value).toBe(
      2,
    );
    // A prompt run plays at the experiment's number.
    expect(resolvedAtOnce(prompt("gpt-4o"), { maxConcurrency: 7 }).value).toBe(
      7,
    );
    expect(resolvedAtOnce(prompt("gpt-4o"), {}).value).toBeNull();
  });

  it("time per try is the model's latency, p50 to 1.5 × p50", () => {
    const [lo, hi] = trySecondsAt("claude-opus-4-1");
    expect(lo).toBe(MODEL_LATENCY_MS["claude-opus-4-1"] / 1000);
    expect(hi).toBeCloseTo(lo * 1.5, 9);
    expect(trySecondsAt("no-such-model")).toBeNull();
    expect(minutesFor(60, 1, [9, 13.5])).toEqual([9, 14]);
    expect(minutesFor(60, 3, [9, 13.5])).toEqual([3, 5]);
  });

  it("a judge call is priced at its judge model (the engine's rule)", () => {
    expect(judgeCallUsd(null)).toBe(JUDGE_COST_PER_CALL);
    expect(judgeCallUsd("gpt-4o-mini")).toBeCloseTo(JUDGE_COST_PER_CALL, 12);
    expect(judgeCallUsd("gpt-4.1")).toBeGreaterThan(JUDGE_COST_PER_CALL * 10);
    const [a, b] = judgesOf([
      { name: "Tone" },
      { name: "Grounded", config: { judge_model: "gpt-4.1" } },
    ]);
    expect(a.cost_per_call).toBe(JUDGE_COST_PER_CALL);
    expect(b.cost_per_call).toBe(judgeCallUsd("gpt-4.1"));
  });

  it("replay modes carry the engine's cost shares", () => {
    expect(replayOf({ replay: { mode: "score_only" } }).share).toBe(0);
    expect(replayOf({ replay: { mode: "rollout" } }).share).toBe(0.45);
    expect(replayOf({})).toBeNull();
  });
});

describe("one estimator (options-truth L3 G3 R1)", () => {
  const evals = [{ name: "Task success" }];
  it("Details prices your code's model calls the same as the headline", () => {
    const cards = [code("gpt-4.1", { tries: 2, concurrency: 3 })];
    const line = costLine({
      promptConfig: cards,
      rows: 8,
      trials: 1,
      evals,
      history: [],
      maxConcurrency: 10,
    });
    const per = costPerTry("gpt-4.1");
    expect(line.text).toContain(
      `16 attempts (8 rows × 2 tries) · ≈ $${per * 16} at gpt-4.1 prices (billed by your provider)`,
    );
    expect(line.text).not.toContain("$0 in Future AGI");
    expect(line.note).toBe("");
    expect(line.total).toBeCloseTo(per * 16 + 16 * JUDGE_COST_PER_CALL, 9);
    expect(line.story.headline).toMatch(
      /^Expected ≈ \$0\.4768 \(rough\) = \$0\.4672 runs \+ \$0\.0096 judges/,
    );
    expect(ESTIMATE_HELP.join(" ")).toContain(
      "7,000 input + 1,900 output tokens per try",
    );
    expect(ESTIMATE_HELP.join(" ")).not.toContain("1,200 tokens");
  });

  it("minutes move with the model and the at-a-time", () => {
    const mins = (model, conc) =>
      runEstimateLines({
        cards: [code(model, { tries: 2, concurrency: conc })],
        rows: 30,
        trials: 1,
        estimate: {},
      }).minutes;
    expect(mins("claude-opus-4-1", 1)).not.toEqual(mins("gpt-4o-mini", 1));
    expect(mins("gpt-4.1", 1)[1]).toBeGreaterThan(mins("gpt-4.1", 3)[1]);
  });

  it("each run plays its own tries; the experiment's At most caps at a time", () => {
    const cards = [
      code("gpt-4.1", { tries: 3, concurrency: 4 }),
      code("gpt-4.1", { tries: 2, concurrency: 4 }),
    ];
    const line = costLine({
      promptConfig: cards,
      rows: 20,
      trials: 1,
      evals,
      history: [],
      maxConcurrency: 1,
    });
    expect(line.estimate.calls).toBe(100);
    expect(line.estimate.judge_calls).toBe(100);
    const [a, b] = line.story.lines;
    expect(a.text).toContain("60 tries");
    expect(b.text).toContain("40 tries");
    expect(a.text).toContain(
      "1 at a time (the experiment runs at most 1 at once; you set 4)",
    );
  });

  it("prompt runs: rows × trials × p50 ÷ At once", () => {
    const at = (n) =>
      costLine({
        promptConfig: [prompt("gpt-4o-mini")],
        rows: 20,
        trials: 1,
        evals,
        history: [],
        maxConcurrency: n,
      }).story.minutes;
    expect(at(1)[0]).toBeGreaterThan(at(10)[0]);
  });

  it("a replay run costs its mode's share; score only bills the judges only", () => {
    const hist = [
      {
        target_key: "prompt|p1|gpt-4o-mini",
        cost_per_attempt: 0.01,
        p50_ms: 2000,
        completed_at: "1",
      },
    ];
    const at = (mode) =>
      costLine({
        promptConfig: [prompt("gpt-4o-mini")],
        rows: 10,
        trials: 1,
        evals,
        history: hist,
        maxConcurrency: 10,
        replay: replayOf({ replay: { mode } }),
      }).story;
    // v2.1: a prompt try is priced by the run engine (history does not
    // override it); the replay share scales that price.
    const full = at("full").runsUsd;
    expect(full).toBeGreaterThan(0);
    expect(at("rollout").runsUsd).toBeCloseTo(full * 0.45, 9);
    expect(at("score_only").runsUsd).toBe(0);
    expect(at("score_only").judgeUsd).toBeGreaterThan(0);
  });

  it("carry-over prices only the new rows, like the headline", () => {
    const cards = [code("gpt-4.1", { tries: 2 })];
    const carry = carryOverLine({
      source: { experimentId: "e", label: "#3", versionNumber: 1, rows: 24 },
      versionNumber: 2,
      rows: 30,
      promptConfig: cards,
      trials: 1,
      history: [],
    });
    expect(carry.toRun).toBe(6);
    const head = costLine({
      promptConfig: cards,
      rows: carry.toRun,
      trials: 1,
      evals: [],
      history: [],
    });
    expect(carry.usd).toBeCloseTo(head.story.runsUsd, 9);
    expect(carry.text).toContain("billed by your provider");
    expect(carry.text).not.toContain("not included");
  });

  // dataset integration FE-0: the per-minute voice estimate (and its variants) is gone.
  it("a voice card is not priced per minute", () => {
    const out = runEstimateLines({
      cards: [
        {
          targetType: "voice",
          name: "Acme voice",
          r4: { kind: "voice", slot: { voice: { variants: [{}, {}] } } },
        },
      ],
      rows: 10,
      trials: 1,
      estimate: {},
    });
    expect(out.runCount).toBe(1);
    expect(out.lines[0].text).not.toMatch(/calls ×|variants/);
  });
});
