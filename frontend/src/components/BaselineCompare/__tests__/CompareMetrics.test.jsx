import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { AGENT_TYPES } from "src/sections/agents/constants";
import CompareMetrics from "../CompareMetrics";

const metrics = [
  {
    id: "m1",
    metric: "duration",
    value: 89.741,
    change: 2,
    percentageChange: 3,
  },
  { id: "m2", metric: "talk_ratio", value: 0.03, change: null },
];

describe("CompareMetrics", () => {
  it("uses voice labels and wording for a voice simulation", () => {
    render(
      <CompareMetrics
        data={metrics}
        isLoading={false}
        simulationCallType={AGENT_TYPES.VOICE}
      />,
    );

    expect(screen.getByText("Call Duration")).toBeInTheDocument();
    expect(screen.getByText("Talk Ratio")).toBeInTheDocument();
    expect(screen.getByText("from baseline call")).toBeInTheDocument();
  });

  it("uses chat labels and wording for a chat simulation", () => {
    render(
      <CompareMetrics
        data={metrics}
        isLoading={false}
        simulationCallType={AGENT_TYPES.CHAT}
      />,
    );

    expect(screen.getByText("Chat Duration")).toBeInTheDocument();
    expect(screen.getByText("from baseline chat")).toBeInTheDocument();
  });

  it("formats float values to two decimals", () => {
    render(
      <CompareMetrics
        data={metrics}
        isLoading={false}
        simulationCallType={AGENT_TYPES.VOICE}
      />,
    );

    expect(screen.getByText("89.74")).toBeInTheDocument();
  });

  it("renders nothing when there are no metrics", () => {
    const { container } = render(
      <CompareMetrics data={[]} isLoading={false} simulationCallType="voice" />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders the skeleton strip while loading", () => {
    render(<CompareMetrics data={undefined} isLoading />);

    expect(screen.getByText("Performance overview")).toBeInTheDocument();
  });
});
