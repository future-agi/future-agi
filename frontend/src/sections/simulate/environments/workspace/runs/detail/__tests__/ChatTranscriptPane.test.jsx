import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ChatTranscriptPane from "../ChatTranscriptPane";

describe("ChatTranscriptPane", () => {
  it("marks a tool row apart from the customer, and a hand-off as a hand-off", () => {
    render(
      <ChatTranscriptPane
        turns={[
          { role: "customer", text: "Where is my order?" },
          {
            role: "tool",
            text: "get_order",
            toolCalls: [{ name: "get_order" }],
          },
          {
            role: "tool",
            text: "Billing",
            toolCalls: [{ name: "Billing", delegated_to: "Billing" }],
          },
        ]}
      />,
    );

    expect(screen.getByText(/· Hand-off$/)).toBeInTheDocument();
    const tool = screen.getByText(/· Tool$/).parentElement;
    const customer = screen.getByText(/· Customer$/).parentElement;
    expect(getComputedStyle(tool).borderLeftColor).not.toBe(
      getComputedStyle(customer).borderLeftColor,
    );
  });
});
