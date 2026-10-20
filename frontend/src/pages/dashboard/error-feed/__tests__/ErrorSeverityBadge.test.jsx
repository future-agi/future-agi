import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ErrorSeverityBadge from "../components/ErrorSeverityBadge";

describe("Feed severity assessment", () => {
  it("does not present a default as an assessed Medium", () => {
    render(
      <ErrorSeverityBadge
        severity="medium"
        source="default"
        assessmentStatus="pending"
      />,
    );
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.queryByText("Medium")).not.toBeInTheDocument();
  });
  it("shows insufficient evidence as unassessed", () => {
    render(
      <ErrorSeverityBadge
        severity="medium"
        source="default"
        assessmentStatus="insufficient_evidence"
      />,
    );
    expect(screen.getByText("Unassessed")).toBeInTheDocument();
  });
  it("keeps an existing assessed grade during refresh", () => {
    render(
      <ErrorSeverityBadge
        severity="high"
        source="llm"
        assessmentStatus="pending"
      />,
    );
    expect(screen.getByText("High")).toBeInTheDocument();
  });
  it("preserves legacy and manual severity display", () => {
    render(
      <ErrorSeverityBadge
        severity="low"
        source="manual"
        assessmentStatus="manual"
      />,
    );
    expect(screen.getByText("Low")).toBeInTheDocument();
  });
});
