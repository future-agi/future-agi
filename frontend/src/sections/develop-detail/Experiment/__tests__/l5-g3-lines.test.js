// options-truth L5 G3: the wizard's own counts and lines. One count per
// screen (executions sum per run's own tries), a run-level cap reads as the
// run's, an endpoint card says its cap, and the empty-reference warning
// counts the rows that run and names the chosen column.
import { describe, expect, it } from "vitest";
import { executionSummary } from "../runPlan";
import { capText, emptyInputLine, emptyInputWarnings } from "../reviewModel";
import { costLine, runEstimateLines } from "../costEstimateModel";
import { judgeModelOf } from "../estimateInputs";

describe("L5 G3 · one count per screen", () => {
  it("sums executions per run when runs carry their own tries", () => {
    expect(
      executionSummary({
        examples: 20,
        trials: 2,
        runs: 2,
        perRun: [
          { label: "#9A", tries: 2 },
          { label: "#9B", tries: 3 },
        ],
      }),
    ).toBe("20 rows · #9A ×2 · #9B ×3 = 100 tries");
    // Same tries everywhere: the plain product, unchanged.
    expect(
      executionSummary({
        examples: 20,
        trials: 2,
        runs: 2,
        perRun: [
          { label: "#9A", tries: 2 },
          { label: "#9B", tries: 2 },
        ],
      }),
    ).toBe("20 rows × 2 tries × 2 runs = 80 tries");
  });

  it("a prompt (Trials 2) and an HTTP card (tries 3) estimate 40 + 60 tries", () => {
    const cards = [
      {
        targetType: "prompt",
        promptId: "p",
        model: [{ value: "gpt-4o-mini" }],
      },
      {
        targetType: "http",
        r4: { kind: "http", entryId: "http", limits: { tries: 3 } },
      },
    ];
    const line = costLine({
      promptConfig: cards,
      rows: 20,
      trials: 2,
      evals: [],
    });
    expect(line.estimate.calls).toBe(100);
    expect(line.estimate.per_target.map((t) => t.attempts)).toEqual([40, 60]);
  });
});

describe("L5 G3 · cap wording", () => {
  it("a run-level cap reads as the run's, with its share a try", () => {
    expect(capText(5, { runLevel: true, tries: 60 })).toBe(
      "$5.00 for the run of 60 tries (≈ $0.08333333333333333 a try)",
    );
    expect(capText(0.05, { runLevel: true, tries: 24 })).toMatch(
      /^\$0\.05\d* for the run of 24 tries \(≈ \$0\.002\d* a try\)$/,
    );
    // A per-try cap keeps its own words.
    expect(capText(1)).toBe("$1.00 per try");
  });

  it("an endpoint run line says its run cap", () => {
    const cards = [
      {
        targetType: "http",
        r4: {
          kind: "http",
          entryId: "http",
          limits: { tries: 3, maxCostUsd: 0.05 },
        },
      },
    ];
    const line = costLine({
      promptConfig: cards,
      rows: 8,
      trials: 1,
      evals: [],
    });
    const story = runEstimateLines({
      cards,
      rows: 8,
      trials: 1,
      estimate: line.estimate,
      ordinal: 9,
    });
    expect(story.lines[0].text).toMatch(
      /stops at \$0\.05\d* for the run of 24 tries \(≈ \$0\.002\d* a try\)/,
    );
  });
});

describe("L5 G3 · empty reference over the rows that run", () => {
  const cols = [
    { id: "c-intent", name: "intent" },
    { id: "c-msg", name: "customer_message" },
  ];
  const rows = Array.from({ length: 20 }, (_, i) => ({
    "c-intent": { cell_value: i === 3 ? "" : "refund" },
    "c-msg": { cell_value: "hi" },
  }));
  it("names the chosen Expected answers column for Correctness", () => {
    const evals = [{ name: "Correctness", config: { mapping: {} } }];
    const [w] = emptyInputWarnings(evals, rows, cols, {
      referenceColumnId: "c-intent",
    });
    expect(emptyInputLine(w, "row", { selected: true })).toBe(
      "Correctness needs intent · empty on 1 of 20 selected rows · those rows show Skipped",
    );
    // Expected = None: no reference to check, no warning.
    expect(
      emptyInputWarnings(evals, rows, cols, { referenceColumnId: null }),
    ).toEqual([]);
  });
});

describe("L5 G3 · judges from each eval's definition", () => {
  it("reads the eval's own model before the default judge", () => {
    expect(judgeModelOf({ model: "gpt-4o" })).toBe("gpt-4o");
    expect(judgeModelOf({ config: { model: "gpt-4.1" } })).toBe("gpt-4.1");
    expect(judgeModelOf({ judge_model: "o3", model: "gpt-4o" })).toBe("o3");
    expect(judgeModelOf({})).toBeNull();
    const line = costLine({
      promptConfig: [
        {
          targetType: "prompt",
          promptId: "p",
          model: [{ value: "gpt-4o-mini" }],
        },
      ],
      rows: 10,
      trials: 1,
      evals: [{ model: "gpt-4o" }, { name: "Tone" }, { type: "check" }],
    });
    // Two judge calls a try (the check is free), at gpt-4o and gpt-4o-mini.
    expect(line.estimate.judge_calls).toBe(20);
    expect(line.estimate.judge_models).toEqual(["gpt-4o", "gpt-4o-mini"]);
    expect(line.story.judges).toMatch(/at gpt-4o, gpt-4o-mini\)$/);
  });
});

describe("L5 G3 · a TTS run is priced by the text its clips speak", () => {
  it("mean characters of the filled template over the sample rows", async () => {
    const { mediaUnitsOf } = await import("../estimateInputs");
    const cards = [
      {
        experimentType: "tts",
        messages: [
          { role: "system", content: "ignored" },
          { role: "user", content: [{ type: "text", text: "{{line}}" }] },
        ],
        model: [{ value: "tts-1-hd", voices: ["alloy", "echo", "shimmer"] }],
      },
    ];
    const columns = [{ id: "c1", name: "line" }];
    const rows = [
      { c1: { cell_value: "x".repeat(100) } },
      { c1: { cell_value: "y".repeat(300) } },
    ];
    expect(mediaUnitsOf({ cards, rows, columns })).toEqual({ chars: 200 });
    const line = costLine({
      promptConfig: cards,
      rows: 20,
      trials: 1,
      evals: [],
      mediaUnits: mediaUnitsOf({ cards, rows, columns }),
    });
    // 3 voices × 20 clips × 200 chars at $0.03 per 1K characters.
    expect(line.total).toBeCloseTo(3 * 20 * 0.2 * 0.03, 6);
    expect(line.text).toMatch(/^60 clips \(20 rows × 1 trial × 3 runs\)/);
  });
});

describe("L5 G3 · cost line: unpriced and per-model prices", () => {
  const card = (model) => ({
    targetType: "prompt",
    promptId: "p",
    model: [{ value: model }],
  });
  const lineOf = (model, customPrices) =>
    runEstimateLines({
      cards: [card(model)],
      rows: 10,
      trials: 1,
      estimate: costLine({
        promptConfig: [card(model)],
        rows: 10,
        trials: 1,
        evals: [],
        customPrices,
      }).estimate,
      customPrices,
    });
  it("a model with no price says so, never a guess", () => {
    const story = lineOf("my-custom-llm");
    expect(story.lines[0].text).toMatch(/~\$— · no price for my-custom-llm/);
    expect(story.expected).toBe(0);
  });
  it("prices scale with the model (gpt-4o-mini < gpt-4.1 < opus)", () => {
    const usd = ["gpt-4o-mini", "gpt-4.1", "claude-opus-4-1"].map(
      (m) => lineOf(m).expected,
    );
    expect(usd[0]).toBeLessThan(usd[1]);
    expect(usd[1]).toBeLessThan(usd[2]);
    // A custom model at its stored prices.
    expect(
      lineOf("acme-llm", { "acme-llm": [40, 160] }).expected,
    ).toBeGreaterThan(usd[2]);
  });
});
