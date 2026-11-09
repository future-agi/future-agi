import { describe, expect, it } from "vitest";
import { fmtCost, fmtCostDelta } from "../fmt";
import {
  fmtMoney,
  fmtTick,
  fmtValue,
} from "../../Analysis/workbench/workbenchFormat";
import { fmtMetric } from "../../DerivedMetrics/formulaModel";
import { fCurrency } from "src/utils/format-number";
import {
  formatBalance,
  formatExactCost,
  formatCostCell,
  formatCostTick,
  formatCostUpperBound,
  costTotal,
} from "src/sections/gateway/utils/formatters";
import { formatMicros } from "src/sections/gateway/mcp/calls/toolCalls";
import { formatPerMillion } from "src/sections/gateway/models/catalog";
import { money as gridMoney } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import { formatUsd as rowMoney } from "src/sections/develop-detail/Experiment/tryOnOneRowModel";
import { formatUsd as codeMoney } from "src/sections/develop-detail/Experiment/runFromCodeModel";
import { usdText } from "src/sections/evals/alignment/judgeCost";
import { money as artifactMoney } from "src/sections/experiment-detail/results/drawer/artifactsModel";
import { formatValueWithConfig } from "src/sections/dashboards/widgetUtils";

// Batch 2: concrete census counterexamples run against the inherited r3 state.
describe("MONEY31 batch 2 decimal counterexamples", () => {
  it.each([
    ["S022/S254/S270/S272-S277 fCurrency", fCurrency],
    ["legacy balance", formatBalance],
    ["legacy exactCost", formatExactCost],
    ["legacy cost cell", formatCostCell],
    ["legacy tick", formatCostTick],
    ["legacy upper bound", formatCostUpperBound],
    ["S217/S221/S222/S225 catalog", formatPerMillion],
    ["S115/S116 grid", gridMoney],
    ["S126/S127 try row", rowMoney],
    ["S131 run from code", codeMoney],
    ["S145-S147 judge", usdText],
    ["S183 artifacts", artifactMoney],
  ])("%s preserves a sub-cent decimal", (_name, format) => {
    expect(format(0.000123456789)).toBe("$0.000123456789");
    expect(format("0.0001234567890123456789")).toBe(
      "$0.0001234567890123456789",
    );
  });
  it("S215/S216 micros conversion does not lose a cent's residual digits", () => {
    expect(formatMicros("1234567")).toBe("$1.234567");
    expect(formatMicros("9007199254740993000123")).toBe(
      "$9007199254740993.000123",
    );
  });
  it("S111-S114 money widgets ignore lossy decimal/abbreviation settings", () => {
    expect(
      formatValueWithConfig("0.000123456789", { unit: "$", decimals: 2 }),
    ).toBe("$0.000123456789");
    expect(
      formatValueWithConfig("1000.000123", { unit: "$", abbreviation: true }),
    ).toBe("$1000.000123");
  });
  it("partial totals preserve the exact known decimal lower bound", () => {
    expect(
      costTotal("1.234567890123456789", { unpriced: 1, requests: 2 }).text,
    ).toBe("≥ $1.234567890123456789");
  });
});

// MONEY-QUAL-31 add.1 (a)-(c): preserve the API decimal, not a rounded Number.
// These tests import existing modules so missing implementation is not RED.
describe("MONEY31 exact per-item costs", () => {
  it.each([
    [0.000123, "$0.000123"],
    ["0.000123", "$0.000123"],
    ["0.0001234567890123456789", "$0.0001234567890123456789"],
    ["1.234567", "$1.234567"],
    ["1000.000123", "$1000.000123"],
    ["9007199254740993.000123", "$9007199254740993.000123"],
    [-0.000123, "-$0.000123"],
    ["-1.234567", "-$1.234567"],
    ["1.23e-7", "$0.000000123"],
    [0, "$0.00"],
    [-0, "$0.00"],
    ["0", "$0.00"],
    [1000, "$1000.00"], // Plain style is unchanged by ruling 528(3).
    [null, "—"],
    [undefined, "—"],
    [NaN, "—"],
    [Infinity, "—"],
    ["not money", "—"],
    ["", "—"],
  ])("fmtCost(%s) retains exact money", (value, expected) => {
    expect(fmtCost(value)).toBe(expected);
    expect(fmtCost(value)).not.toContain("≈");
  });

  it("preserves decimal strings in signed monetary deltas", () => {
    expect(fmtCostDelta("0.0001234567890123456789")).toBe(
      "+$0.0001234567890123456789",
    );
    expect(fmtCostDelta("-0.0001234567890123456789")).toBe(
      "−$0.0001234567890123456789",
    );
    expect(fmtCostDelta(0)).toBe("±$0.00");
  });
});

describe("MONEY31 independent shared formatting counterexamples", () => {
  it("retains the full per-item workbench cost", () => {
    expect(fmtMoney(0.000123)).toBe("$0.000123");
    expect(fmtMoney("0.0001234567890123456789")).toBe(
      "$0.0001234567890123456789",
    );
    expect(fmtMoney(-0.000123)).toBe("-$0.000123");
  });

  it("retains USD values in generic metric formatting", () => {
    expect(fmtMetric(0.000123, "usd")).toBe("$0.000123");
    expect(fmtMetric("1.234567890123456789", "usd")).toBe(
      "$1.234567890123456789",
    );
  });

  it("retains exact USD in workbench detail strings", () => {
    expect(fmtValue({ unit: "usd" }, 0.000123, { precise: true })).toBe(
      "$0.000123",
    );
  });

  it("does not silently round USD axes", () => {
    // Exact ticks are permitted; compact ticks need a marked approximate value
    // plus a separate keyboard/touch-accessible total at their UI binding.
    const tick = fmtTick({ unit: "usd" }, 0.000123);
    expect(tick === "$0.000123" || tick.startsWith("≈")).toBe(true);
  });
});
