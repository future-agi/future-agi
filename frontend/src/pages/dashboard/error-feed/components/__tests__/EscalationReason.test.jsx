import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import EscalationReason from "../EscalationReason";
import ErrorStatusChip from "../ErrorStatusChip";

describe("escalation explanation", () => {
  it("shows when the evidence was evaluated so stale evidence is identifiable", () => {
    render(
      <EscalationReason
        issue={{
          status: "for_review",
          status_origin: "system",
          escalation: {
            current_count: 1,
            affected_trace_count: 1,
            baseline: 0,
            entry_threshold: 20,
            unit: "findings",
            evaluated_at: "2026-10-14T12:10:00Z",
          },
        }}
      />,
    );
    expect(screen.getByText(/Last evaluated/)).toBeVisible();
    expect(
      screen.getByText(/Last evaluated/).querySelector("time"),
    ).toHaveAttribute("dateTime", "2026-10-14T12:10:00Z");
  });
  it("shows independent current volume, breadth, baseline and threshold", () => {
    render(
      <EscalationReason
        issue={{
          status: "escalating",
          status_origin: "system",
          escalation: {
            current_count: 20,
            affected_trace_count: 8,
            baseline: 5,
            entry_threshold: 20,
            recovery_threshold: 8,
            unit: "findings",
            entry_confirmation_minutes: 15,
          },
        }}
      />,
    );
    expect(
      screen.getByText(
        "20 findings across 8 traces in the evaluated hour; baseline 5/hour; escalation threshold 20. Growth confirmed for 15 minutes.",
      ),
    ).toBeVisible();
  });
  it("shows user intent instead of stale automatic evidence", () => {
    render(
      <EscalationReason
        issue={{
          status: "escalating",
          status_origin: "manual",
          escalation: { current_count: 99 },
        }}
      />,
    );
    expect(screen.getByText("Set by a user")).toBeVisible();
    expect(screen.queryByText(/99/)).not.toBeInTheDocument();
  });
  it("uses failed evaluation units and an honest zero baseline", () => {
    render(
      <EscalationReason
        issue={{
          status: "escalating",
          status_origin: "system",
          escalation: {
            current_count: 20,
            affected_trace_count: 2,
            baseline: 0,
            entry_threshold: 20,
            unit: "failed_evaluations",
            entry_confirmation_minutes: 15,
          },
        }}
      />,
    );
    expect(
      screen.getByText(/20 failed evaluations across 2 traces/),
    ).toBeVisible();
    expect(screen.getByText(/no occurrences in the baseline/)).toBeVisible();
  });
  it("does not invent an automatic reason for unknown legacy ownership", () => {
    const { container } = render(
      <EscalationReason
        issue={{ status: "escalating", status_origin: "legacy_unknown" }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
  it("does not label an unknown status Escalating", () => {
    render(<ErrorStatusChip status="unknown" />);
    expect(screen.queryByText("Escalating")).not.toBeInTheDocument();
    expect(screen.getByText("For review")).toBeVisible();
  });
});
