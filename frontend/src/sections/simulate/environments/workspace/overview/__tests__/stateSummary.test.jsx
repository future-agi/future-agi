import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";

import { render } from "src/utils/test-utils";
import StateSummary from "../StateSummary";

describe("StateSummary", () => {
  it("uses the real §6 counts when provided (backed env)", () => {
    render(
      <StateSummary
        env={{ rules: ["a", "b"] }}
        envState={{ scenarios: [], evals: [], runs: [] }}
        counts={{ scenarios: 1, evaluations: 8, runs: 1, hardRules: 13 }}
        onGo={vi.fn()}
      />,
    );
    // Evaluations (8) and Hard rules (13) are unique, proving the passed counts
    // win over the empty client store (which would show 0 evals and 2 rules).
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("13")).toBeInTheDocument();
    expect(screen.queryByText("2")).toBeNull();
  });

  it("falls back to the client store when no counts are passed", () => {
    render(
      <StateSummary
        env={{ rules: ["a", "b", "c"] }}
        envState={{ scenarios: [1, 2], evals: [1], runs: [] }}
        onGo={vi.fn()}
      />,
    );
    // Hard rules 3 (env.rules length), unique among the store-derived numbers.
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  const finished = {
    id: "ex-14",
    label: "Run 14",
    agentVersion: "v1",
    status: "passed",
    total: 4,
    passed: 3,
  };

  it("shows the newest run from the runs list, by name and agent version only", () => {
    render(
      <StateSummary
        env={{ rules: [] }}
        envState={{ runs: [] }}
        counts={{ runs: 14 }}
        latestRun={finished}
        onGo={vi.fn()}
      />,
    );
    expect(screen.getByText("Latest run")).toBeInTheDocument();
    expect(screen.getByText("Run 14 · v1")).toBeInTheDocument();
    expect(screen.queryByText("75%")).toBeNull();
    expect(screen.queryByText("pass rate")).toBeNull();
  });

  it("has no latest-run card before the first run", () => {
    render(
      <StateSummary env={{ rules: [] }} envState={{ runs: [] }} counts={{ runs: 0 }} onGo={vi.fn()} />,
    );
    expect(screen.queryByText("Latest run")).toBeNull();
  });
});
