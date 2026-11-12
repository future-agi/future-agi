// integration (RV-09): the pairwise card the drawer now shows blind — Response 1 /
// Response 2 on the seeded side, no run names, cost, latency, scores or
// judge line; a vote names the side the reviewer saw. Blind off restores.
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import PairwiseCard from "../PairwiseCard";

const runA = { run_id: "a", label: "#3A", name: "Support Agent v2" };
const runB = { run_id: "b", label: "#4A", name: "Support Agent v3" };
const res = (out, cost) => ({
  status: "completed",
  output: out,
  cost,
  latency_ms: 1234,
  tokens: 321,
  scores: { "eval:correctness": { value: 1, display: "Pass" } },
});
const props = (blind, left = "B") => ({
  runA,
  runB,
  resultA: res("Reply from the baseline", 0.0123),
  resultB: res("Reply from the candidate", 0.0456),
  colors: { a: "#111", b: "#222" },
  blind,
  onBlindChange: () => {},
  left,
  preference: {
    choice: null,
    votes: { A: 0, B: 0, tie: 0 },
    judge: { choice: "B", model: "gpt-4o", reason: "More complete" },
  },
  onChoose: vi.fn(),
  onClear: () => {},
  comment: "",
  onCommentChange: () => {},
  onAutoAdvanceChange: () => {},
});

describe("PairwiseCard blind (RV-09)", () => {
  it("names no run and shows no cost, latency, scores or judge line", () => {
    const p = props(true, "B");
    const { container } = render(<PairwiseCard {...p} />);
    const t = container.textContent;
    expect(t).toContain("Response 1");
    expect(t).toContain("Response 2");
    [
      "#3A",
      "#4A",
      "Support Agent",
      "$0.0",
      "1.2 s",
      "321 tok",
      "LLM judge",
      "gpt-4o",
    ].forEach((leak) => expect(t).not.toContain(leak));
    // Seeded side B is on the left: Response 1 is the candidate's reply.
    const r1 = t.indexOf("Response 1");
    expect(t.indexOf("Reply from the candidate")).toBeGreaterThan(r1);
    expect(t.indexOf("Reply from the candidate")).toBeLessThan(
      t.indexOf("Reply from the baseline"),
    );
    // The buttons name the sides the card shows (review fix: not
    // Left / Right): `Response 1 is better` maps to side B (on the left).
    expect(screen.queryByRole("button", { name: /Left is better/ })).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: /Response 1 is better/ }),
    );
    expect(p.onChoose).toHaveBeenCalledWith("B");
    fireEvent.click(
      screen.getByRole("button", { name: /Response 2 is better/ }),
    );
    expect(p.onChoose).toHaveBeenLastCalledWith("A");
  });

  it("blind hides the team's choice (no name, no highlighted button)", () => {
    const team = {
      choice: "B",
      votes: { A: 0, B: 1, tie: 0 },
      judge: null,
    };
    const { container, unmount } = render(
      <PairwiseCard {...props(true, "B")} preference={team} />,
    );
    const t = container.textContent;
    expect(t).not.toContain("Current choice");
    expect(t).not.toMatch(/Response \d is better\./);
    expect(t).toContain(
      "1 reviewer has voted; their choice stays hidden while Blind is on.",
    );
    expect(
      container.querySelectorAll('[data-review-vote][aria-pressed="true"]')
        .length,
    ).toBe(0);
    unmount();
    const open = render(
      <PairwiseCard {...props(false, "A")} preference={team} />,
    );
    expect(open.container.textContent).toContain(
      "Current choice from 1 reviewer: #4A is better.",
    );
  });

  it("blind off restores names and the judge line", () => {
    const { container } = render(<PairwiseCard {...props(false, "A")} />);
    const t = container.textContent;
    expect(t).toContain("#3A");
    expect(t).toContain("#4A");
    expect(t).toContain("LLM judge");
  });
});
