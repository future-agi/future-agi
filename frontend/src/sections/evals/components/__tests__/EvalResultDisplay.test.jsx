import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import EvalResultDisplay from "../EvalResultDisplay";

describe("EvalResultDisplay", () => {
  it("returns null (renders nothing) when result is not provided", () => {
    const { container } = render(<EvalResultDisplay result={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a green 'Pass' chip for a raw 'Passed' Pass/Fail output", () => {
    render(
      <EvalResultDisplay
        result={{ output: "Passed", output_type: "Pass/Fail" }}
      />,
    );

    const chip = screen.getByText("Pass");
    expect(chip).toBeInTheDocument();
    // MUI Chip color is applied via the `MuiChip-color*` class.
    expect(chip.closest(".MuiChip-root")).toHaveClass("MuiChip-colorSuccess");
  });

  it("renders a red 'Fail' chip for a raw 'Failed' Pass/Fail output", () => {
    render(
      <EvalResultDisplay
        result={{ output: "Failed", output_type: "Pass/Fail" }}
      />,
    );

    const chip = screen.getByText("Fail");
    expect(chip).toBeInTheDocument();
    expect(chip.closest(".MuiChip-root")).toHaveClass("MuiChip-colorError");
  });

  it("renders a numeric score with two decimal places", () => {
    render(
      <EvalResultDisplay
        result={{ output: 0.8234, output_type: "score", reason: "Solid answer" }}
      />,
    );

    expect(screen.getByText("Score")).toBeInTheDocument();
    expect(screen.getByText("0.82")).toBeInTheDocument();
  });

  it("renders the Explanation reason text block", () => {
    render(
      <EvalResultDisplay
        result={{
          output: "Passed",
          output_type: "Pass/Fail",
          reason: "The response correctly answered the question.",
        }}
      />,
    );

    expect(screen.getByText("Explanation")).toBeInTheDocument();
    expect(
      screen.getByText("The response correctly answered the question."),
    ).toBeInTheDocument();
  });

  it("does not render an Explanation block when there is no reason", () => {
    render(
      <EvalResultDisplay result={{ output: "Passed", output_type: "Pass/Fail" }} />,
    );
    expect(screen.queryByText("Explanation")).not.toBeInTheDocument();
  });

  it("returns null for a code-eval-shaped result with no output, score, or reason", () => {
    const { container } = render(<EvalResultDisplay result={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a code eval's boolean score via result.score when result.output is absent", () => {
    render(<EvalResultDisplay result={{ score: 1, output_type: "score" }} />);
    expect(screen.getByText("1.00")).toBeInTheDocument();
  });
});
