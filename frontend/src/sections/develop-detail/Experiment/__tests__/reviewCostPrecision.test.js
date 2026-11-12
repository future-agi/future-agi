// The Review step's cost headline prints estimates as money: a float sum or
// difference never shows its rounding noise, tiny amounts keep two
// significant figures, and the "<amount> runs + <amount> judges" order stays.
import { describe, expect, it } from "vitest";
import { runEstimateLines } from "../costEstimateModel";

const NOISE = /\d{7,}/;

describe("Review cost headline reads as money", () => {
  it("a prompt run on a workspace model: no float noise in the runs part", () => {
    const out = runEstimateLines({
      cards: [
        {
          promptId: "p-1",
          name: "walk-capital",
          versionLabel: "v1",
          model: [{ value: "liquid/lfm-3-1.7b" }],
        },
      ],
      rows: 3,
      trials: 1,
      maxConcurrency: 10,
      customPrices: { "liquid/lfm-3-1.7b": [0.1, 0.1] },
      estimate: {
        calls: 3,
        judge_calls: 6,
        judge_cost_usd: 0.0036,
        judge_models: ["liquid/lfm-3-1.7b"],
        per_target: [{ cost_usd: 0.000212, from_history: false }],
      },
    });
    expect(out.headline).toMatch(
      /^Expected ≈ \$0\.0038 \(rough\) = \$0\.00021 runs \+ \$0\.0036 judges/,
    );
    expect(out.headline).not.toMatch(NOISE);
    expect(out.lines[0].text).toContain("expected ≈ $0.00021 in Future AGI");
    expect(out.judges).toContain("≈ $0.0036");
    expect(out.short).toMatch(/^≈ \$0\.0038 \(rough\)/);
    expect(out.chip).toMatch(/^1 run · ~\$0\.0038/);
  });

  it("a harness run with a tiny estimate: runs and judges in order, worst case clean", () => {
    const out = runEstimateLines({
      cards: [
        {
          targetType: "harness",
          name: "Claude Code",
          runName: "Claude Code",
          r4: {
            mode: "local",
            entryId: "claude_code",
            limits: { concurrency: 2 },
          },
        },
      ],
      rows: 3,
      trials: 1,
      estimate: {
        judge_cost_usd: 0.0036,
        per_target: [{ cost_usd: 0.000212 }],
      },
    });
    expect(out.headline).toMatch(
      /^Expected ≈ \$0\.0038 = \$0\.00021 runs \+ \$0\.0036 judges · worst case \$3\.0036 \(caps\)/,
    );
    expect(out.headline).not.toMatch(NOISE);
    expect(out.lines[0].text).toContain("expected ≈ $0.00021 ·");
  });

  it("amounts of a cent and more drop float noise but keep their digits", () => {
    const out = runEstimateLines({
      cards: [
        {
          targetType: "harness",
          name: "Claude Code",
          runName: "Claude Code",
          r4: {
            mode: "local",
            entryId: "claude_code",
            limits: { concurrency: 2 },
          },
        },
      ],
      rows: 30,
      trials: 1,
      estimate: {
        judge_cost_usd: 0.2,
        per_target: [{ cost_usd: 0.1 }],
      },
    });
    expect(out.headline).toMatch(
      /^Expected ≈ \$0\.30 = \$0\.10 runs \+ \$0\.20 judges · worst case \$30\.20 \(caps\)/,
    );
    expect(out.headline).not.toMatch(NOISE);
    expect(out.worstMath).toBe(
      "Worst case $30.20 = 30 tries × $1.00 + $0.20 judges",
    );
  });
});
