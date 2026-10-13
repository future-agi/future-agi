import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import RunConversationMessages from "./RunConversationMessages";

describe("RunConversationMessages", () => {
  it("renders server messages with role labels and preserves plain text", () => {
    render(
      <RunConversationMessages
        runId="run-a"
        messages={[
          {
            id: "u",
            conversationId: "run-a",
            role: "user",
            text: "What is 2 + 2?",
          },
          { id: "a", conversationId: "run-a", role: "agent", text: "4" },
          {
            id: "foreign",
            conversationId: "run-b",
            role: "agent",
            text: "must not appear",
          },
        ]}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Conversation" }),
    ).toBeInTheDocument();
    expect(screen.getByText("What is 2 + 2?")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.queryByText("must not appear")).not.toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
  });

  it("shows an honest empty state", () => {
    render(<RunConversationMessages runId="run-a" messages={[]} />);
    expect(
      screen.getByText("No conversation messages are available yet."),
    ).toBeInTheDocument();
  });
});
