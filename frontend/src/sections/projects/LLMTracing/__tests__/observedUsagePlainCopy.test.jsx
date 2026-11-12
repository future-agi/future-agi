import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CostCell from "../Renderers/CostCell";
import TokenCell from "../Renderers/TokenCell";
import SessionCellRenderer from "../../SessionsView/SessionCellRenderer";
import { CaptureCoverageNotice } from "src/components/traceDetail/TraceDetailPanel";

// W10-TRACING-OTHER-U01: usage that counts only the model calls we received
// (GitHub Copilot CLI agent runs) must read in plain words, number first.
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

const coverage = (request_count) => ({
  scope: "observed_requests",
  source_summary_present: true,
  request_count,
  coverage_unknown: true,
});
const JARGON =
  /Observed-only|observed request|nonadditive|Capture coverage|Source summar/i;
const RUN_SUMMARY =
  "The agent's own run summary isn't added, so the real total may be higher.";
const THREE_CALLS = `Counts only the 3 model calls we received. ${RUN_SUMMARY}`;

describe("W10-TRACING-OTHER-U01 received-calls usage reads in plain words", () => {
  it.each([
    ["cost", CostCell, 0.0123, "$0.0123"],
    ["tokens", TokenCell, 4210, "4,210 tokens"],
  ])(
    "trace %s cell shows the number first and explains it on hover",
    async (_, Cell, value, shown) => {
      const { container } = render(
        <Cell value={value} data={{ _captureCoverage: coverage(3) }} />,
      );
      expect(container.textContent).toBe(`${shown}*`);
      fireEvent.mouseOver(screen.getByText(`${shown}*`));
      const tooltip = await screen.findByRole("tooltip");
      expect(tooltip).toHaveTextContent(`${shown}. ${THREE_CALLS}`);
      expect(tooltip).not.toHaveTextContent(JARGON);
    },
  );

  it.each([
    ["total_cost", 1.25, "$1.25"],
    ["total_tokens", 43071, "43,071 tokens"],
  ])(
    "session %s cell shows the number first and explains it on hover",
    async (colId, value, shown) => {
      const { container } = render(
        <SessionCellRenderer
          column={{ colId }}
          value={value}
          data={{ capture_coverage: coverage(3) }}
        />,
      );
      expect(container.textContent).toBe(`${shown}*`);
      fireEvent.mouseOver(screen.getByText(`${shown}*`));
      const tooltip = await screen.findByRole("tooltip");
      expect(tooltip).toHaveTextContent(`${shown}. ${THREE_CALLS}`);
      expect(tooltip).not.toHaveTextContent(JARGON);
    },
  );

  it("says plainly when no model calls were received", async () => {
    render(<CostCell value={0} data={{ _captureCoverage: coverage(0) }} />);
    fireEvent.mouseOver(screen.getByText("$0.00*"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      `$0.00. We haven't received any model calls. ${RUN_SUMMARY}`,
    );
  });

  it.each([
    [3, "Usage counts only the 3 model calls we received"],
    [1, "Usage counts only the 1 model call we received"],
    [0, "We haven't received any model calls"],
  ])(
    "drawer banner explains %s received model calls without jargon",
    (count, lead) => {
      render(<CaptureCoverageNotice coverage={coverage(count)} />);
      const note = screen.getByRole("note", { name: "Capture coverage" });
      expect(note).toHaveTextContent(lead);
      expect(note).toHaveTextContent(
        "Some calls may not have been sent, and the agent's own run summary isn't added, so the real total may be higher.",
      );
      expect(note).not.toHaveTextContent(JARGON);
    },
  );
});
