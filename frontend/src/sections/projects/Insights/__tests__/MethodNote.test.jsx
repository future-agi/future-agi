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
});
