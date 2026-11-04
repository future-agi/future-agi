import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ChatTranscriptPane from "../ChatTranscriptPane";

describe("ChatTranscriptPane", () => {
  it("labels a tool call as one and marks it apart from the customer", () => {
    render(
      <ChatTranscriptPane
        turns={[
          { role: "customer", text: "Where is my order?" },
          { role: "tool", text: "get_order", toolCalls: [] },
        ]}
      />,
    );

    const tool = screen.getByText(/· Tool call$/).parentElement;
    const customer = screen.getByText(/· Customer$/).parentElement;
    expect(getComputedStyle(tool).borderLeftColor).not.toBe(
      getComputedStyle(customer).borderLeftColor,
    );
  });
});
