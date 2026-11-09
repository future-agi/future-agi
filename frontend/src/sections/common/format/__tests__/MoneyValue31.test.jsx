import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MoneyValue from "../MoneyValue";
import TwoRunScatterChart from "../../Analysis/workbench/charts/TwoRunScatterChart";
import {
  formatCompactMoney,
  formatExactMoney,
  formatMoneyDelta,
  sumMoneyValues,
  multiplyMoneyValues,
} from "../money";
import { deltaBarRows } from "../../Analysis/workbench/charts/deltaBarsModel";
import { deltaLine, rangeText } from "../../Analysis/workbench/groupNumbers";
import { spendLine, spendSplitText } from "../../Runs/runLedger";

const scatterProps31 = {
  matrix: {
    rows: [
      {
        row_id: "money-row",
        label: "Money row",
        cells: {
          b: { trials: [1], cost_usd: [0.000123456789] },
          c: { trials: [1], cost_usd: [0.000234567891] },
        },
      },
    ],
  },
  measure: {
    key: "cost",
    kind: "numeric",
    unit: "usd",
    label: "Cost",
    higherIsBetter: false,
  },
  baseCol: { key: "b", label: "Baseline", runIds: ["b"] },
  candCol: { key: "c", label: "Candidate", runIds: ["c"] },
  candidates: [{ key: "c", label: "Candidate", runIds: ["c"] }],
  onCand: () => {},
  onOpenRows: () => {},
};

describe("MONEY31 S059 accessible point detail", () => {
  it("reveals exact scatter point details by keyboard", async () => {
    const user = userEvent.setup();
    render(<TwoRunScatterChart {...scatterProps31} />);
    const point = screen.queryByRole("button", { name: /Money row.*exact/i });
    expect(point).not.toBeNull();
    await user.tab();
    expect(point).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("$0.000123456789");
    expect(screen.getByRole("status")).toHaveTextContent("$0.000234567891");
    expect(screen.getByRole("status")).toBeVisible();
  });
  it("reveals exact scatter point details by touch", async () => {
    const user = userEvent.setup();
    render(<TwoRunScatterChart {...scatterProps31} />);
    const point = screen.queryByRole("button", { name: /Money row.*exact/i });
    expect(point).not.toBeNull();
    await user.pointer({ keys: "[TouchA]", target: point });
    expect(screen.getByRole("status")).toHaveTextContent("$0.000123456789");
    expect(screen.getByRole("status")).toHaveTextContent("$0.000234567891");
    expect(screen.getByRole("status")).toBeVisible();
  });
});
afterEach(cleanup);

// MONEY-QUAL-31 add.1 (a)-(c), including 528(3)'s distinct plain/grouped styles.
describe("MONEY31 shared exact and aggregate contracts", () => {
  it.each([
    ["0.0001234567890123456789", "$0.0001234567890123456789"],
    ["9007199254740993.000123", "$9007199254740993.000123"],
    ["-1.234567", "-$1.234567"],
    ["1.23e-7", "$0.000000123"],
    ["1000", "$1000.00"],
  ])("keeps the authoritative decimal %s", (value, expected) => {
    expect(formatExactMoney(value)).toBe(expected);
  });

  it("groups large ledger amounts without rounding their fractional digits", () => {
    expect(formatExactMoney("1000.000123", { grouping: true })).toBe(
      "$1,000.000123",
    );
    expect(
      formatExactMoney("9007199254740993.000123", { grouping: true }),
    ).toBe("$9,007,199,254,740,993.000123");
  });

  it("keeps unknown amounts distinct from zero", () => {
    for (const missing of [null, undefined, NaN, Infinity, "", "not money"]) {
      expect(formatExactMoney(missing)).toBe("—");
      expect(formatCompactMoney(missing)).toBe("—");
    }
    expect(formatExactMoney(0)).toBe("$0.00");
    expect(formatExactMoney(-0)).toBe("$0.00");
  });

  it("marks compact aggregates but never exact values", () => {
    expect(formatCompactMoney("0.000123")).toMatch(/^≈\s*\$/);
    expect(formatCompactMoney("1234567.000123")).toMatch(/^≈\s*\$/);
    expect(formatExactMoney("0.000123")).not.toContain("≈");
  });

  it("does not coerce decimal-string deltas through Number", () => {
    expect(formatMoneyDelta("0.0001234567890123456789")).toBe(
      "+$0.0001234567890123456789",
    );
    expect(formatMoneyDelta("-0.0001234567890123456789")).toBe(
      "−$0.0001234567890123456789",
    );
    expect(formatMoneyDelta(0)).toBe("±$0.00");
  });
});

// Added after the preserved 37-case RED run: client-side component totals.
describe("MONEY31 exact component totals", () => {
  it("adds decimal strings without concatenation or float rounding", () => {
    expect(sumMoneyValues(["0.1", "0.2", "0.0000000000000000001"])).toBe(
      "0.3000000000000000001",
    );
    expect(
      formatExactMoney(sumMoneyValues(["9007199254740993.000123", "0.000001"])),
    ).toBe("$9007199254740993.000124");
  });
  it("preserves negative components and distinguishes unknown", () => {
    expect(formatExactMoney(sumMoneyValues(["0.000123", "-0.000124"]))).toBe(
      "-$0.000001",
    );
    expect(sumMoneyValues(["1", null])).toBeNull();
    expect(formatCompactMoney(null, { missing: null })).toBeNull();
  });
});

describe("MONEY31 decimal strings through display models", () => {
  it("keeps raw cost deltas and intervals separate from chart coordinates", () => {
    const rows = deltaBarRows(
      {
        metrics: [
          {
            key: "ops:cost",
            name: "Cost",
            family: "ops",
            vs_baseline: [
              {
                run_id: "x",
                delta: "0.0001234567890123456789",
                ci95: "0.000001",
              },
            ],
          },
        ],
      },
      "x",
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].text).toBe("+$0.0001234567890123456789 ± $0.000001");
  });
  it("preserves group deltas and calculates monetary half-intervals exactly", () => {
    const measure = { unit: "usd" };
    expect(
      deltaLine(measure, {
        kind: "mean",
        delta: "-0.0001234567890123456789",
        low: "0.000123000000000001",
        high: "0.000125000000000001",
        n: 3,
      }),
    ).not.toBeNull();
    expect(
      deltaLine(measure, {
        kind: "mean",
        delta: "-0.0001234567890123456789",
        low: "0.000123000000000001",
        high: "0.000125000000000001",
        n: 3,
      }).short,
    ).toBe("Δ −$0.0001234567890123456789 (±$0.000001)");
    expect(
      rangeText(measure, {
        low: "9007199254740993.000123",
        high: "9007199254740993.000125",
      }),
    ).toBe("$9007199254740993.000123–$9007199254740993.000125");
    expect(formatCompactMoney(-0)).toBe("≈ $0.00");
  });
  it("keeps authoritative spend strings and exact breakdown subtraction", () => {
    const usd = "0.0001234567890123456789";
    expect(
      spendLine({ estimate: { usd, tries: 1 }, actual: { usd, tries: 0 } }),
    ).toBe(`Estimated $${usd} · spent $${usd} so far · 0 of 1 tries`);
    expect(
      spendSplitText({
        usd: "9007199254740993.000125",
        judge_usd: "9007199254740993.000123",
      }),
    ).toBe(
      "$9007199254740993.000125 spent: $0.000002 tries + $9007199254740993.000123 judge calls",
    );
  });
});

describe("MONEY31 accessible exact-value affordance", () => {
  it("renders per-item money exactly without an approximation marker", () => {
    render(<MoneyValue value="0.0001234567890123456789" />);
    const amount = screen.queryByText("$0.0001234567890123456789");
    expect(amount).not.toBeNull();
    expect(amount).toBeVisible();
    expect(screen.queryByText(/≈/)).not.toBeInTheDocument();
  });

  it("reveals an aggregate's exact value on keyboard focus", async () => {
    const user = userEvent.setup();
    render(<MoneyValue value="0.0001234567890123456789" aggregate />);
    const trigger = screen.queryByRole("button", { name: /exact/i });
    expect(trigger).not.toBeNull();
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveTextContent("≈");
    await user.tab();
    expect(trigger).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "$0.0001234567890123456789",
    );
    await waitFor(() => expect(screen.getByRole("tooltip")).toBeVisible());
  });

  it("reveals exact aggregate money on touch tap, not just native title", async () => {
    const user = userEvent.setup();
    render(<MoneyValue value="1000.000123" aggregate />);
    const trigger = screen.queryByRole("button", { name: /exact/i });
    expect(trigger).not.toBeNull();
    expect(trigger).toBeInTheDocument();
    await user.pointer({ keys: "[TouchA]", target: trigger });
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "$1000.000123",
    );
    await waitFor(() => expect(screen.getByRole("tooltip")).toBeVisible());
  });

  it("reveals exact aggregate money on click", async () => {
    const user = userEvent.setup();
    render(<MoneyValue value="-0.000123" aggregate />);
    const trigger = screen.queryByRole("button", { name: /exact/i });
    expect(trigger).not.toBeNull();
    expect(trigger).toBeInTheDocument();
    await user.click(trigger);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("-$0.000123");
    await waitFor(() => expect(screen.getByRole("tooltip")).toBeVisible());
  });
});

// Additional post-RED coverage for decimal wire values and exact unit scaling.
it("keeps source decimal strings in the touch-sized scatter row picker", async () => {
  const user = userEvent.setup();
  const matrix = {
    rows: [
      {
        row_id: "money-row",
        label: "Money row",
        cells: {
          b: { trials: [1], cost_usd: ["0.0001234567890123456789"] },
          c: { trials: [1], cost_usd: ["0.0002345678901234567891"] },
        },
      },
    ],
  };
  render(<TwoRunScatterChart {...scatterProps31} matrix={matrix} />);
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Exact row values" }),
    "money-row",
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "$0.0001234567890123456789",
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "$0.0002345678901234567891",
  );
  expect(screen.getByRole("status")).toBeVisible();
});

it("scales cents and micros without converting raw decimal strings", () => {
  expect(
    formatExactMoney(multiplyMoneyValues("9007199254740993000123", "0.000001")),
  ).toBe("$9007199254740993.000123");
  expect(formatExactMoney(multiplyMoneyValues("-0.0123456789", "0.01"))).toBe(
    "-$0.000123456789",
  );
  expect(multiplyMoneyValues(null, "0.01")).toBeNull();
});
