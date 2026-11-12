/* eslint-disable react/prop-types */
// This release refuses token, message and cost limits per row: their fields
// stay on the run card, a value in one shows the release line and blocks the
// Configuration step (so Run), and the server refuses the same keys with the
// same sentence. The time limit alone runs, and its help says a row stops
// after the call in flight ends once the limit has passed, and that a prompt
// row whose model call fails is not run again while a time limit is set.
import React from "react";
import { describe, expect, it } from "vitest";
import { useForm } from "react-hook-form";
import { render, screen } from "src/utils/test-utils";
import RowLimits from "../RowLimits";
import {
  TIME_LIMIT_HELP,
  TIME_LIMIT_NO_RETRY,
  UNAVAILABLE_ROW_LIMITS,
  UNAVAILABLE_ROW_LIMITS_TEXT,
  rowLimitStepIssues,
} from "../rowLimitsModel";
import { configurationIssues } from "src/sections/develop-detail/Experiment/stepIssues";

function Card({ limits }) {
  const form = useForm({
    defaultValues: { promptConfig: [{ rowLimits: limits }] },
  });
  return <RowLimits control={form.control} fieldPrefix="promptConfig.0" />;
}

const run = (rowLimits) => ({
  name: "Limits",
  experimentType: "llm",
  promptConfig: [
    {
      targetType: "prompt",
      name: "support",
      promptId: "1b2c3d4e-0000-4000-8000-000000000001",
      promptVersion: "1b2c3d4e-0000-4000-8000-000000000002",
      model: [{ value: "gpt-4o-mini" }],
      rowLimits,
    },
  ],
});

describe("per-row limits in this release", () => {
  it("names the refused limits and the exact line, one list for both", () => {
    expect([...UNAVAILABLE_ROW_LIMITS]).toEqual([
      "tokens",
      "messages",
      "cost_usd",
    ]);
    expect(UNAVAILABLE_ROW_LIMITS_TEXT).toBe(
      "Token, message and cost limits aren't available in this release. Clear them to run.",
    );
  });

  it.each([{ tokens: 500 }, { messages: 2 }, { cost_usd: 0.05 }])(
    "keeps the field and shows the line for %j",
    (limits) => {
      render(<Card limits={limits} />);
      const [key] = Object.keys(limits);
      expect(screen.getByTestId(`row-limit-${key}`)).toBeInTheDocument();
      expect(screen.getByTestId("row-limits-issues")).toHaveTextContent(
        UNAVAILABLE_ROW_LIMITS_TEXT,
      );
      // No time limit: no time help and no no-retry line.
      expect(
        screen.queryByTestId("row-limits-time-help"),
      ).not.toBeInTheDocument();
      expect(rowLimitStepIssues({ rowLimits: limits })).toEqual([
        UNAVAILABLE_ROW_LIMITS_TEXT,
      ]);
      expect(configurationIssues(run(limits))).toContain(
        UNAVAILABLE_ROW_LIMITS_TEXT,
      );
    },
  );

  it("runs with a time limit alone and says when it stops a row", () => {
    render(<Card limits={{ timeout_s: 30 }} />);
    expect(screen.queryByTestId("row-limits-issues")).not.toBeInTheDocument();
    expect(screen.getByTestId("row-limits-time-help")).toHaveTextContent(
      TIME_LIMIT_HELP,
    );
    expect(TIME_LIMIT_HELP).toMatch(/after the call in flight ends/);
    expect(TIME_LIMIT_HELP).toMatch(/before and after each model call/);
    // A failed model call is not run again while a time limit is set.
    expect(screen.getByTestId("row-limits-time-help")).toHaveTextContent(
      TIME_LIMIT_NO_RETRY,
    );
    expect(TIME_LIMIT_NO_RETRY).toBe(
      "In a prompt run with a time limit, a row whose model call fails is not run again, even after a temporary failure such as a rate limit.",
    );
    expect(rowLimitStepIssues({ rowLimits: { timeout_s: 30 } })).toEqual([]);
    expect(configurationIssues(run({ timeout_s: 30 }))).not.toContain(
      UNAVAILABLE_ROW_LIMITS_TEXT,
    );
  });
});
