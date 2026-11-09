// W6-16 (A1): the per-row limit rules the wizard, the create body and the
// engine share (rowLimitsModel), and the card's create-body keys.
import { describe, expect, it } from "vitest";
import {
  normalizeRowLimits,
  rowLimitIssues,
  rowLimitStepIssues,
  rowLimitsBodyIssues,
  rowLimitsExtras,
  rowLimitsLine,
  stopOf,
  stopText,
  wouldStop,
} from "../rowLimitsModel";
import { cardExtrasOf } from "../cardExtras";

const steps = [
  { n: 1, type: "llm", tokens: 300, cost: 0.001, ms: 400 },
  { n: 2, type: "tool_call", ms: 5 },
  {
    n: 3,
    type: "llm",
    tokens: { input: 250, output: 100, total: 350 },
    cost: 0.002,
    ms: 500,
  },
  { n: 4, type: "tool_call", ms: 5 },
  { n: 5, type: "llm", tokens: 200, cost: 0.003, ms: 600 },
  { n: 6, type: "end_state", ms: 0 },
];

describe("W6 A1 · row limits", () => {
  it("keeps positive values only, whole numbers where they must be", () => {
    expect(
      normalizeRowLimits({
        tokens: "600",
        messages: "",
        cost_usd: 0.05,
        timeout_s: -1,
      }),
    ).toEqual({ tokens: 600, cost_usd: 0.05 });
    expect(normalizeRowLimits(null)).toEqual({});
    expect(rowLimitIssues({ tokens: "2.5", cost_usd: "0" })).toEqual([
      "Tokens must be a whole number",
      "Cost must be above 0",
    ]);
    expect(rowLimitIssues({ tokens: 10 })).toEqual([]);
  });

  // Review M3: a shown value is never rounded or dropped on its way to the
  // run; an invalid one blocks the step, the Try and the create call.
  it("never rounds a value: an invalid limit is an issue that blocks", () => {
    expect(normalizeRowLimits({ tokens: 12.5, messages: -2 })).toEqual({});
    expect(
      rowLimitsExtras({ rowLimits: { tokens: "12.5", messages: "-2" } }),
    ).toEqual({
      row_limits: { tokens: 12.5, messages: -2 },
    });
    expect(
      rowLimitStepIssues({ rowLimits: { tokens: "12.5", messages: "-2" } }),
    ).toEqual([
      "Limits per row: Tokens must be a whole number",
      "Limits per row: Messages must be above 0",
    ]);
    expect(rowLimitStepIssues({ rowLimits: { tokens: "600" } })).toEqual([]);
    expect(rowLimitStepIssues({})).toEqual([]);
    expect(rowLimitsBodyIssues({ tokens: 0, foo: 1 })).toEqual([
      "foo is not a limit (tokens, messages, cost_usd, timeout_s)",
      "Tokens must be above 0",
    ]);
    expect(rowLimitsBodyIssues([600])).toHaveLength(1);
    expect(rowLimitsBodyIssues(undefined)).toEqual([]);
  });

  it("stops the crossing step at the limit; keeps the first N messages", () => {
    // 300 + 350 tokens before step 5 (200): 600 crosses inside step 3.
    expect(stopOf(steps, { tokens: 600 })).toEqual({
      index: 2,
      kind: "tokens",
      at: 600,
      fraction: 300 / 350,
    });
    expect(stopOf(steps, { tokens: 850 })).toBeNull();
    expect(stopOf(steps, { tokens: 650 })).toEqual({
      index: 3,
      kind: "tokens",
      at: 650,
      fraction: 1,
    });
    expect(stopOf(steps, { tokens: 700 })).toEqual({
      index: 4,
      kind: "tokens",
      at: 700,
      fraction: 50 / 200,
    });
    const c = stopOf(steps, { cost_usd: 0.002 });
    expect(c).toMatchObject({ index: 2, kind: "cost_usd", at: 0.002 });
    expect(c.fraction).toBeCloseTo(0.5, 9);
    expect(stopOf(steps, { messages: 2 })).toEqual({
      index: 1,
      kind: "messages",
      at: 2,
      fraction: 1,
    });
    expect(stopOf(steps, { messages: 5 })).toBeNull();
    expect(stopOf(steps, { timeout_s: 1 })).toBeNull();
    expect(wouldStop({ trajectory: { steps } }, { messages: 3 })).toEqual({
      index: 2,
      kind: "messages",
      at: 3,
      fraction: 1,
    });
  });

  it("says why in the run's words", () => {
    expect(stopText({ kind: "tokens", at: 2000 })).toBe(
      "Stopped at 2,000 tokens",
    );
    expect(stopText({ kind: "messages", at: 12 })).toBe(
      "Stopped at 12 messages",
    );
    expect(stopText({ kind: "cost_usd", at: 0.05 })).toBe(
      "Stopped at $0.050 for this row",
    );
    expect(stopText({ kind: "timeout_s", at: 120 })).toBe(
      "Timed out after 120 s",
    );
    expect(rowLimitsLine({ tokens: 600, timeout_s: 30 })).toBe(
      "Limits per row · 600 tokens · 30 s",
    );
    expect(rowLimitsLine({})).toBeNull();
  });

  it("prompt and agent cards send row_limits; other cards do not", () => {
    const rowLimits = { tokens: "600", messages: null };
    expect(rowLimitsExtras({ rowLimits })).toEqual({
      row_limits: { tokens: 600 },
    });
    expect(cardExtrasOf({ targetType: "prompt", rowLimits })).toEqual({
      row_limits: { tokens: 600 },
    });
    expect(
      cardExtrasOf({ targetType: "agent", rowLimits: { messages: 4 } }),
    ).toEqual({
      row_limits: { messages: 4 },
    });
    expect(cardExtrasOf({ targetType: "http", rowLimits })).toEqual({});
    expect(cardExtrasOf({ targetType: "prompt" })).toEqual({});
  });
});
