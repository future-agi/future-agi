import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BuildStrip from "../BuildStrip";
import { stepOfStage } from "../../buildSteps";

describe("BuildStrip", () => {
  it("says the build is stopping at the step it reached", () => {
    render(
      <BuildStrip
        name="support"
        step={stepOfStage("generating_scenarios", { readsAgent: false })}
        stopping
        startedAt="2026-11-25T10:00:00Z"
      />,
    );
    expect(
      screen.getByText(
        /Stopping the build of support · Writing and checking scenarios · Generating scenarios/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Stopping takes a moment")).toBeInTheDocument();
    expect(
      screen.getByText("Stopping at Generating scenarios"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/so far/)).toBeNull();
  });
});
