import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PreflightReport } from "../PreflightStatus";

describe("PreflightReport", () => {
  it("shows nothing for an empty report when the caller gives no fallback", () => {
    // The environment workspace never passes one, so an empty report stays quiet there.
    const { container } = render(<PreflightReport report={{ checks: [] }} />);
    expect(container).toBeEmptyDOMElement();
    const { container: none } = render(<PreflightReport report={null} />);
    expect(none).toBeEmptyDOMElement();
  });

  it("shows the lines, not the fallback, when the report has some", () => {
    render(
      <PreflightReport
        report={{
          checks: [{ id: "c", label: "Credentials", status: "failed" }],
        }}
        fallback="No detail"
      />,
    );
    expect(screen.getByText("Credentials: failed")).toBeInTheDocument();
    expect(screen.queryByText("No detail")).not.toBeInTheDocument();
  });

  it("shows the fallback for an empty or missing report", () => {
    render(<PreflightReport report={null} fallback="No detail" />);
    expect(screen.getByText("No detail")).toBeInTheDocument();
  });
});
