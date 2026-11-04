import { describe, expect, it } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ChatTranscriptPane from "../ChatTranscriptPane";

const plainChat = [
  { role: "customer", text: "One ride please" },
  { role: "agent", text: "Booked" },
];

const tabLabels = () =>
  screen.getAllByRole("tab").map((tab) => tab.textContent);

describe("ChatTranscriptPane", () => {
  it("names a sub-actor with their role and how they were heard, and filters to them", async () => {
    render(
      <ChatTranscriptPane
        customerName="Maya"
        turns={[
          ...plainChat,
          {
            role: "sub_actor",
            speakerName: "Rowan",
            actorRole: "roommate",
            channel: "audio_mix",
            text: "Make it two",
          },
        ]}
      />,
    );
    expect(screen.getAllByText("Rowan · roommate")).toHaveLength(2);
    expect(screen.getAllByText("Heard nearby")).toHaveLength(2);
    expect(screen.getByText("Maya · Customer")).toBeInTheDocument();
    expect(screen.queryByText(/sub_actor/)).toBeNull();
    expect(tabLabels()).toEqual(["All", "Assistant", "Customer", "Others"]);
    await userEvent.click(screen.getByRole("tab", { name: "Others" }));
    expect(screen.getByText("Make it two")).toBeInTheDocument();
    expect(screen.queryByText("Booked")).toBeNull();
  });

  it("shows just Someone for a sub-actor an older call kept no name for", () => {
    render(
      <ChatTranscriptPane
        turns={[...plainChat, { role: "sub_actor", text: "Hello?" }]}
      />,
    );
    expect(screen.getAllByText("Someone")).toHaveLength(2);
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("renders a chat with only the customer and the agent as before", () => {
    render(<ChatTranscriptPane customerName="Maya" turns={plainChat} />);
    expect(tabLabels()).toEqual(["All", "Assistant", "Customer"]);
    expect(screen.queryByText("On the call:")).toBeNull();
  });

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

  it("marks a sub-actor's row apart from the customer's", () => {
    render(
      <ChatTranscriptPane
        turns={[
          { role: "customer", text: "One ride please" },
          { role: "sub_actor", speakerName: "Rowan", text: "Make it two" },
        ]}
      />,
    );
    const customer = screen.getByText("One ride please").parentElement;
    const other = screen.getByText("Make it two").parentElement;
    expect(getComputedStyle(other).borderLeftColor).not.toBe(
      getComputedStyle(customer).borderLeftColor,
    );
  });
});
