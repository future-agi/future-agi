import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/iconify", () => ({
  default: (props) => (
    <span data-icon={props.icon} className={props.className} />
  ),
}));

import TraceChip, { TRACE_CHIP_LOOK } from "../TraceChip";
import {
  cellTraceChipProps,
  noTraceTooltip,
  traceSummaryLabel,
} from "../traceChipModel";

const result = (summary) => ({ trace_summary: [summary] });

describe("cellTraceChipProps", () => {
  it("ok: steps and tool calls, one shared tool mode", () => {
    expect(
      cellTraceChipProps(
        result({
          status: "ok",
          steps: 6,
          tool_calls: 2,
          llm_calls: 3,
          tool_modes: ["mock", "mock"],
        }),
        1,
      ),
    ).toMatchObject({
      state: "ok",
      label: "6 steps · 2 tool calls",
      mode: "mock",
    });
    expect(
      cellTraceChipProps(
        result({
          status: "ok",
          steps: 6,
          tool_calls: 2,
          llm_calls: 3,
          tool_modes: ["mock", "live"],
        }),
      ).mode,
    ).toBeNull();
  });
  it("harness turns and a single LLM call", () => {
    expect(
      cellTraceChipProps(
        result({
          status: "ok",
          steps: 22,
          turns: 14,
          tool_calls: 7,
          llm_calls: 15,
          tool_modes: [],
        }),
      ).label,
    ).toBe("14 turns · 7 tool calls");
    expect(
      cellTraceChipProps(
        result({
          status: "ok",
          steps: 1,
          tool_calls: 0,
          llm_calls: 1,
          tool_modes: [],
        }),
      ).label,
    ).toBe("1 LLM call");
    expect(traceSummaryLabel({ steps: 1, tool_calls: 1, llm_calls: 0 })).toBe(
      "1 step · 1 tool call",
    );
  });
  it("running, error, timeout", () => {
    expect(
      cellTraceChipProps(result({ status: "running", steps: 3 })).label,
    ).toBe("Running · step 3");
    expect(
      cellTraceChipProps(
        result({
          status: "error",
          steps: 4,
          error: { step: 4, text: "lookup_customer 404" },
        }),
      ).label,
    ).toBe("Failed at step 4 · lookup_customer 404");
    expect(
      cellTraceChipProps(
        result({
          status: "error",
          steps: 19,
          error: { step: 19, text: "timed out", timeout_s: 600 },
        }),
      ).label,
    ).toBe("Timed out at step 19 · 600 s");
  });
  it("none with a tooltip by source kind", () => {
    const none = cellTraceChipProps({}, 1, { sourceKind: "http" });
    expect(none).toMatchObject({ state: "none", label: "No trace" });
    expect(none.tooltip).toBe(noTraceTooltip("http"));
    expect(noTraceTooltip("http")).toBe(
      'This endpoint didn\'t return a trace id. Set "Trace id path" on the run.',
    );
    expect(noTraceTooltip("upload")).toBe(
      "This upload has no trace_id. Add trace_id or spans to the payload.",
    );
    expect(noTraceTooltip("agent")).toBe(
      "No trace was recorded for this result.",
    );
    expect(cellTraceChipProps(result({ status: "ok" }), 2).state).toBe("none");
  });
  it("carried and infra", () => {
    expect(
      cellTraceChipProps(
        result({ status: "carried", carried_from: "#2B", steps: 6 }),
      ).label,
    ).toBe("Carried from #2B");
    expect(
      cellTraceChipProps(result({ status: "infra", reason: "setup failed" }))
        .label,
    ).toBe("Didn't run · setup failed");
  });
});

describe("TraceChip", () => {
  it.each(Object.keys(TRACE_CHIP_LOOK))("renders the %s look", (state) => {
    const { container } = render(
      <TraceChip state={state} label={`L ${state}`} />,
    );
    const root = container.querySelector(".trace-chip");
    expect(root.className).toContain(`trace-chip--${state}`);
    expect(root.getAttribute("aria-label")).toBe(`L ${state}`);
  });
  it("dashed borders for none and carried, 60% for carried", () => {
    expect(TRACE_CHIP_LOOK.none.border).toBe("dashed");
    expect(TRACE_CHIP_LOOK.carried).toMatchObject({
      border: "dashed",
      opacity: 0.6,
    });
    expect(TRACE_CHIP_LOOK.error.tone).toBe("error");
    expect(TRACE_CHIP_LOOK.infra.tone).toBe("warning");
  });
  it("Enter opens; Shift+F10 opens the menu", () => {
    const onOpen = vi.fn();
    const onCopy = vi.fn();
    render(
      <TraceChip
        state="ok"
        label="6 steps · 2 tool calls"
        mode="mock"
        onOpen={onOpen}
        menu={[
          { label: "Copy trace id", icon: "mdi:content-copy", onClick: onCopy },
        ]}
      />,
    );
    const chip = screen.getByRole("button", { name: "6 steps · 2 tool calls" });
    fireEvent.keyDown(chip, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(screen.getByText("mock")).toBeTruthy();
    fireEvent.keyDown(chip, { key: "F10", shiftKey: true });
    fireEvent.click(screen.getByText("Copy trace id"));
    expect(onCopy).toHaveBeenCalledTimes(1);
  });
});
