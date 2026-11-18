import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import MethodNote from "../MethodNote";
import runFixture from "./fixtures/run_sessions_failures.json";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const run = runFixture.result;

const renderNote = (overrides) =>
  render(
    <MethodNote
      run={{ ...run, ...overrides }}
      config={{ lens: "failures" }}
      open
      onToggle={() => {}}
    />,
  );

describe("MethodNote", () => {
  it("says how the sample was drawn and which engine wrote the report", () => {
    renderNote({
      sample_size: 300,
      population_size: 2345,
      settings: { ...run.settings, sampling: "uniform" },
      result: {
        ...run.result,
        engine: { kind: "builtin", version: "builtin/v1" },
      },
    });
    expect(
      screen.getByText("300 of 2,345 sessions, a uniform random sample"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/spread evenly over time/)).toBeNull();
    expect(
      screen.getByText("Built-in engine (builtin/v1)"),
    ).toBeInTheDocument();
  });

  it("keeps 'spread evenly over time' for rows handed over with their times", () => {
    renderNote({
      sample_size: 300,
      population_size: 2345,
      settings: { ...run.settings, sampling: "even_over_time" },
    });
    expect(
      screen.getByText("300 of 2,345 sessions, spread evenly over time"),
    ).toBeInTheDocument();
  });

  it.each([
    [{ credits: 964.57 }, "Charged", "965 credits"],
    [
      { credits: 964.57, unpriced: true },
      "Charged",
      "965 credits, including calls to a model with no listed price, charged at the highest rate",
    ],
    [{ usd: 0.4213 }, "Model cost", "$0.42 at list prices"],
    [
      { usd: 8.04, unpriced: true },
      "Model cost",
      "Not known: a model it used has no listed price",
    ],
  ])("shows what the report cost as recorded: %j", (cost, label, text) => {
    renderNote({ cost_usd: 8.04, result: { ...run.result, cost } });
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(text)).toBeInTheDocument();
    // Never the raw model cost in dollars beside a charge in credits.
    if (cost.credits || cost.unpriced)
      expect(screen.queryByText(/\$8\.04/)).toBeNull();
  });

  it("names the days a custom range read", () => {
    const year = new Date().getFullYear();
    renderNote({
      scope: {
        label: "Custom range · 1 filter",
        preset: "Custom",
        window: [
          new Date(year, 9, 22).toISOString(),
          new Date(year, 9, 23, 23, 59, 59, 999).toISOString(),
        ],
      },
    });
    expect(screen.getByText("Oct 22–23 · 1 filter")).toBeInTheDocument();
    expect(screen.queryByText(/Custom range/)).toBeNull();
  });

  it("shows no cost a run didn't record", () => {
    renderNote({ cost_usd: 8.04, result: { ...run.result, cost: undefined } });
    expect(screen.queryByText("Cost")).toBeNull();
    expect(screen.queryByText(/\$8\.04/)).toBeNull();
  });
});
