import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import TwoRunScatterChart from "../../Analysis/workbench/charts/TwoRunScatterChart";
import { settingsOf, settingsText } from "../../Runs/provenanceModel";
import { runSettingsRows } from "../../Runs/provenanceJson";
import { formatDelta } from "../../Metrics/metricFormat";
import { spendLine } from "../../Runs/runLedger";

afterEach(cleanup);

const usdMetric = { output_type: "usd" };
const scatterProps = {
  measure: { key: "cost", kind: "numeric", unit: "usd", label: "Cost" },
  baseCol: { key: "b", label: "Baseline", runIds: ["b"] },
  candCol: { key: "c", label: "Candidate", runIds: ["c"] },
  candidates: [{ key: "c", label: "Candidate", runIds: ["c"] }],
  onCand: () => {},
  onOpenRows: () => {},
};

describe("MONEY31 EXACT-01 billed scatter population", () => {
  it.each([
    [[1, "error"], ["1", "3"], "$4.00 / 2 ($1.00, $3.00)"],
    [["error"], ["2"], "$2.00"],
    [
      [0, "error", "skipped"],
      ["0.000000000000000001", "3", "99"],
      "$3.000000000000000001 / 2 ($0.000000000000000001, $3.00)",
    ],
  ])(
    "includes billed scored and errored attempts %j",
    (trials, cost_usd, expected) => {
      const cell = { trials, cost_usd };
      const matrix = {
        rows: [
          { row_id: "r", label: "Billed row", cells: { b: cell, c: cell } },
        ],
      };
      render(<TwoRunScatterChart {...scatterProps} matrix={matrix} />);
      const point = screen.queryByRole("button", {
        name: "Billed row exact values",
      });
      expect(point).not.toBeNull();
      fireEvent.focus(point);
      expect(screen.getByRole("status").textContent).toBe(
        `Billed row · Baseline ${expected} → Candidate ${expected}`,
      );
      expect(point.querySelector("title").textContent).toBe(
        screen.getByRole("status").textContent,
      );
      expect(screen.getByRole("status")).not.toHaveTextContent(" / 0");
    },
  );
});

describe("MONEY31 EXACT-02 provenance decimal cap readers", () => {
  it.each(["9007199254740993.000123", "0.0001234567890123456789"])(
    "retains %s through both provenance surfaces",
    (value) => {
      const config = {
        limits: { max_cost_usd: value, max_cost_per_try_usd: value },
      };
      expect(settingsOf(config).max_cost_usd).toBe(value);
      expect(settingsOf(config).max_cost_per_try_usd).toBe(value);
      expect(settingsText(config)).toBe(`$${value} per try · cap $${value}`);
      const rows = runSettingsRows({ config });
      expect(rows).toHaveLength(2);
      expect(rows.map((row) => row.value)).toEqual([`$${value}`, `$${value}`]);
    },
  );
  it.each([0, "0"])("retains genuine zero cap %j", (value) => {
    const config = {
      limits: { max_cost_usd: value, max_cost_per_try_usd: value },
    };
    expect(settingsText(config)).toBe("$0.00 per try · cap $0.00");
    expect(runSettingsRows({ config }).map((row) => row.value)).toEqual([
      "$0.00",
      "$0.00",
    ]);
  });
});

describe("MONEY31 EXACT-03 USD delta unit is representation independent", () => {
  it.each([
    [0.01, 1],
    ["0.01", 1],
    [0.01, "1"],
    ["0.01", "1"],
  ])("relative delta %j with baseline %j", (delta, base) => {
    expect(formatDelta(usdMetric, delta, { base })).toBe("+1%");
  });
  it.each([0, "0", undefined, null])(
    "absolute USD when baseline is zero or absent %j",
    (base) => {
      expect(formatDelta(usdMetric, "0.0001234567890123456789", { base })).toBe(
        "+$0.0001234567890123456789",
      );
    },
  );
  it("rounds a long-precision relative delta without a floating intermediary", () => {
    expect(
      formatDelta(usdMetric, "0.014999999999999999999999", { base: "1" }),
    ).toBe("+1%");
    expect(
      formatDelta(usdMetric, "-0.014999999999999999999999", { base: 1 }),
    ).toBe("−1%");
    expect(formatDelta(usdMetric, "1e-400", { base: "1e-398" })).toBe("+1%");
  });
});

describe("MONEY31 EXACT-04 unknown actual spend is not free", () => {
  it.each([null, undefined, "", "unpriced", NaN, Infinity])(
    "completed unknown %j has no saving",
    (usd) => {
      const text = spendLine({
        estimate: { usd: "1", tries: 1 },
        actual: { usd, done: true, tries: 1 },
      });
      expect(text).toBe("Estimated $1.00 · spent — · 1 try");
      expect(text).not.toContain("%");
      expect(text).not.toContain("spent $0");
    },
  );
  it("does not invent in-progress spend", () => {
    expect(spendLine({ estimate: { usd: "1", tries: 1 } })).toBe(
      "Estimated $1.00 · spent — so far · 0 of 1 tries",
    );
  });
  it.each([0, "0"])("keeps genuine zero spend %j", (usd) => {
    expect(
      spendLine({
        estimate: { usd: "1", tries: 1 },
        actual: { usd, done: true, tries: 1 },
      }),
    ).toBe("Estimated $1.00 · spent $0 (−100%) · 1 try");
  });
  it("compares long-precision spend without Number underflow", () => {
    const tiny = `0.${"0".repeat(399)}1`;
    expect(
      spendLine({
        estimate: { usd: tiny, tries: 1 },
        actual: { usd: tiny, done: true, tries: 1 },
      }),
    ).toBe(`Estimated $${tiny} · spent $${tiny} (±0%) · 1 try`);
  });
});
