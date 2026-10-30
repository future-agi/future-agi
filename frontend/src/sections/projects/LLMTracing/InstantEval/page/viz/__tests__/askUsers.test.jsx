import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { AskPatterns } from "../index";
import { findConsistency, findPatterns, poissonTail } from "../askStats";
import { chartQuestions, runTally } from "../askSummary";
import summary from "./fixtures/realUsers";

// The real 11,502-user question on harbor-scale: each user's latest five
// sessions at most, every user counted once under their rolled-up answer.
// The month holds an outage on Oct 7, 2–5 PM, and 40 users who are rarely
// helped; with five sessions read a user, few of them can stand out.

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });
const questions = chartQuestions([], summary);
const purpose = questions.find((q) => q.answerType === "category");
const resolved = questions.find((q) => q !== purpose);

describe("a user question's counts", () => {
  it("count each user once, so the shares add up to all of them", () => {
    [purpose, resolved].forEach((question) => {
      const run = runTally(summary, question);
      const byLabel = [...run.byLabel.values()].reduce((s, v) => s + v, 0);
      expect(byLabel).toBe(run.answered);
      expect(run.answered + run.failed).toBe(summary.total);
      const shares = [...run.byLabel.values()].map((n) => n / run.answered);
      expect(shares.reduce((s, v) => s + v, 0)).toBeCloseTo(1, 10);
    });
    expect(summary.total).toBe(11502);
    expect(summary.sessions_read).toBe(19480);
    // One answer per user per question: users answered both, once each.
    const cross = summary.cross[purpose.configId][resolved.configId];
    const both = Object.values(cross)
      .flatMap((by) => Object.values(by))
      .reduce((s, v) => s + v, 0);
    expect(both).toBe(summary.answered);
  });
});

describe("What stands out on a user question", () => {
  it("leads with the users the outage hit and folds the rest under them", () => {
    const result = findPatterns({
      summary,
      questions,
      focus: { configId: purpose.configId, answer: null },
      unit: "users",
    });
    const all = [...result.patterns, ...result.more];
    const errors = result.patterns.find((p) =>
      p.text.startsWith("“Order status” for users with a trace error:"),
    );
    expect(errors.text).toBe(
      "“Order status” for users with a trace error: 197 of 343 (57%) vs 33% elsewhere",
    );
    // Product advice is missing among them, so more common elsewhere: the
    // same finding from the other side, under it rather than first.
    const complement = all.find((p) =>
      p.text.startsWith("“Product advice” for users with no trace error:"),
    );
    expect(result.patterns).not.toContain(complement);
    expect(complement.within.key).toBe(errors.key);
    expect(all.indexOf(complement)).toBeGreaterThan(all.indexOf(errors));
    all
      .filter((p) => p.where === "for users with no trace error")
      .forEach((p) => expect(p.within?.key).toBe(errors.key));
  });

  it("counts the users who answered No in every session read", () => {
    const [line, ...others] = findConsistency({
      summary,
      question: resolved,
      answer: "No",
    });
    expect(others).toEqual([]);
    expect(line.text).toBe(
      "15 users answered “No” in every session read (5 of 5) · 53 in 4 of 5",
    );
    expect(line.chance).toBe(
      "At the run's 35% of sessions, chance alone would put about 5 and 45 there.",
    );
    expect(line.q).toBeLessThan(0.05);
    expect(line.select).toEqual({
      filter: {
        group: [
          `consistent:${resolved.configId}|No|5|5`,
          `consistent:${resolved.configId}|No|4|5`,
        ],
      },
      count: 68,
      label: "Users who answered “No” in 5 of 5 or 4 of 5 sessions read",
    });
  });

  it("lists no users for an answer five sessions can't tell apart", () => {
    // At 65% of sessions, 5 of 5 doesn't surely beat the run's share.
    expect(
      findConsistency({ summary, question: resolved, answer: "Yes" }),
    ).toEqual([]);
    // Users who got an answer about as often as chance would put there.
    expect(findConsistency({ summary, question: purpose })).toEqual([]);
  });

  it("shows the consistent users above the callouts, and selects them", () => {
    const onSelect = vi.fn();
    render(
      <ThemeProvider theme={theme}>
        <AskPatterns
          summary={summary}
          questions={questions}
          focus={{ configId: resolved.configId, answer: "No" }}
          running={false}
          unit="users"
          onSelect={onSelect}
          onFocus={vi.fn()}
        />
      </ThemeProvider>,
    );
    const list = screen.getByRole("list", {
      name: "Users who kept getting an answer",
    });
    expect(list).toHaveTextContent(
      "15 users answered “No” in every session read (5 of 5) · 53 in 4 of 5",
    );
    fireEvent.click(within(list).getByRole("button", { name: "Show 68" }));
    expect(onSelect).toHaveBeenCalledWith(
      {
        group: [
          `consistent:${resolved.configId}|No|5|5`,
          `consistent:${resolved.configId}|No|4|5`,
        ],
      },
      "Users who answered “No” in 5 of 5 or 4 of 5 sessions read",
    );
    // The callouts follow, with the errors' one among them.
    expect(
      screen.getByRole("list", { name: "Strongest patterns" }),
    ).toHaveTextContent("“No” for users with a trace error");
  });
});

describe("poissonTail", () => {
  it("is the chance of at least x at a mean", () => {
    expect(poissonTail(0, 3)).toBe(1);
    expect(poissonTail(1, 1)).toBeCloseTo(1 - Math.exp(-1), 12);
    expect(poissonTail(3, 1)).toBeCloseTo(
      1 - Math.exp(-1) * (1 + 1 + 1 / 2),
      12,
    );
    expect(poissonTail(15, 4.86)).toBeLessThan(0.001);
    expect(poissonTail(41, 37)).toBeGreaterThan(0.2);
  });
});
