import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ConversationCard from "../ConversationCard";

describe("ConversationCard", () => {
  it("renders without raw messages instead of crashing", () => {
    render(
      <ConversationCard
        role="assistant"
        content="Your booking is confirmed."
        align="flex-start"
        simulationCallType="text"
      />,
    );

    expect(screen.getByText("Your booking is confirmed.")).toBeInTheDocument();
  });

  it("shows the tool calls and results it is given", () => {
    render(
      <ConversationCard
        role="assistant"
        content="Checking."
        align="flex-start"
        simulationCallType="text"
        rawContent={[
          {
            role: "assistant",
            tool_calls: [
              {
                id: "call_1",
                function: { name: "get_booking", arguments: "{}" },
              },
            ],
          },
          { role: "tool", tool_call_id: "call_1", content: "{}" },
        ]}
      />,
    );

    expect(screen.getByText("get_booking")).toBeInTheDocument();
    expect(screen.getByText("Tool result")).toBeInTheDocument();
  });
});
