import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ConversationCard from "../ConversationCard";

// TH-7460: `ShowComponent` is a plain component, so JSX evaluates its
// children eagerly — before `condition` is ever read. The tool-call block
// therefore ran `rawContent.flatMap(...)` on every render, and every caller
// that omits the optional `rawContent` prop (ViewFullTranscript,
// CallTranscriptView, CompareConversation) crashed the whole tree with
// "Cannot read properties of undefined (reading 'flatMap')".
describe("ConversationCard tool-call block", () => {
  it("renders when rawContent is omitted", () => {
    expect(() =>
      render(
        <ConversationCard
          role="assistant"
          content="Hello there"
          align="flex-start"
        />,
      ),
    ).not.toThrow();

    expect(screen.getByText("Hello there")).toBeInTheDocument();
  });

  it("renders when rawContent is a non-array value", () => {
    expect(() =>
      render(
        <ConversationCard
          role="user"
          content="Plain string content"
          align="flex-end"
          rawContent="not-an-array"
        />,
      ),
    ).not.toThrow();

    expect(screen.getByText("Plain string content")).toBeInTheDocument();
  });

  it("still renders tool calls when rawContent is a populated array", () => {
    render(
      <ConversationCard
        role="assistant"
        content="Looking that up"
        align="flex-start"
        rawContent={[
          {
            role: "assistant",
            tool_calls: [
              {
                id: "call-1",
                function: { name: "get_weather", arguments: '{"city":"NYC"}' },
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText(/get_weather/)).toBeInTheDocument();
  });
});
