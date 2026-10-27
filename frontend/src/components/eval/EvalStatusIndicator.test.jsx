import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import EvalStatusIndicator from "./EvalStatusIndicator";

// One indicator drives every not-yet-scored eval cell across the app, so lock
// each lifecycle state's rendering (and that terminal states render nothing).

describe("EvalStatusIndicator", () => {
  it("renders a full-cell skeleton while running", () => {
    const { container } = render(<EvalStatusIndicator status="running" />);
    expect(container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();
  });

  it("renders a 'Queued' pill when pending", () => {
    render(<EvalStatusIndicator status="pending" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("renders a 'Skipped' chip with the reason behind a tooltip", async () => {
    render(
      <EvalStatusIndicator status="skipped" skippedReason="missing: input" />,
    );
    const chip = screen.getByText("Skipped");
    expect(chip).toBeInTheDocument();
    // Reason lives in a hover tooltip, not inline.
    expect(screen.queryByText("missing: input")).not.toBeInTheDocument();
    fireEvent.mouseOver(chip);
    expect(await screen.findByText("missing: input")).toBeInTheDocument();
  });

  it("renders a 'Skipped' chip with no tooltip when there is no reason", () => {
    render(<EvalStatusIndicator status="skipped" />);
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("renders an 'Error' chip when errored", () => {
    render(<EvalStatusIndicator status="errored" />);
    expect(screen.getByText("Error")).toBeInTheDocument();
  });

  it("treats the legacy 'error' status the same as errored", () => {
    render(<EvalStatusIndicator status="error" />);
    expect(screen.getByText("Error")).toBeInTheDocument();
  });

  it("renders nothing for a terminal/completed state", () => {
    const { container } = render(<EvalStatusIndicator status="completed" />);
    expect(container).toBeEmptyDOMElement();
  });
});
