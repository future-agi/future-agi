import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CompareTranscript from "../CompareTranscript";

const data = {
  baselineSession: {
    conversations: [
      { id: "b1", role: "user", content: "I need a refill" },
      { id: "b2", role: "assistant", content: "Sure, which medication?" },
    ],
  },
  replayedSession: {
    conversations: [{ id: "r1", role: "user", content: "I need a refill" }],
  },
};

describe("CompareTranscript", () => {
  it("renders both columns with their speaker labels", () => {
    render(<CompareTranscript data={data} isLoading={false} />);

    expect(screen.getByText("Baseline")).toBeInTheDocument();
    expect(screen.getByText("Replay")).toBeInTheDocument();
    expect(screen.getAllByText("I need a refill")).toHaveLength(2);
    expect(screen.getByText("Sure, which medication?")).toBeInTheDocument();
  });

  it("pads the shorter side with a placeholder so rows stay aligned", () => {
    render(<CompareTranscript data={data} isLoading={false} />);

    expect(screen.getByText("— no matching turn —")).toBeInTheDocument();
  });

  it("shows an empty state when there is nothing to compare", () => {
    render(<CompareTranscript data={null} isLoading={false} />);

    expect(
      screen.getByText("No comparison transcript available"),
    ).toBeInTheDocument();
  });

  it("keeps the diff toggle off until it is switched on", () => {
    render(<CompareTranscript data={data} isLoading={false} />);

    expect(
      screen.getByRole("checkbox", { name: /show diff/i }),
    ).not.toBeChecked();
    expect(screen.queryByText(/Removals/)).not.toBeInTheDocument();
  });
});
