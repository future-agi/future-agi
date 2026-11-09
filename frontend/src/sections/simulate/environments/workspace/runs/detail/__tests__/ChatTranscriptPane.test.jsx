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
  it("names an actor with their role and how they were heard, and filters to them", async () => {
    render(
      <ChatTranscriptPane
        customerName="Maya"
        turns={[
          ...plainChat,
          {
            role: "actor",
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
    expect(screen.queryByText(/call_actor/)).toBeNull();
    expect(tabLabels()).toEqual(["All", "Assistant", "Customer", "Others"]);
    await userEvent.click(screen.getByRole("tab", { name: "Others" }));
    expect(screen.getByText("Make it two")).toBeInTheDocument();
    expect(screen.queryByText("Booked")).toBeNull();
  });

  it("falls back to All when Others was picked and the next call has none", async () => {
    const { rerender } = render(
      <ChatTranscriptPane
        turns={[...plainChat, { role: "actor", text: "Hello?" }]}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "Others" }));
    rerender(<ChatTranscriptPane turns={plainChat} />);
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent(
      "All",
    );
    expect(screen.getByText("Booked")).toBeInTheDocument();
  });

  it("shows just Someone for an actor an older call kept no name for", () => {
    render(
      <ChatTranscriptPane
        turns={[...plainChat, { role: "actor", text: "Hello?" }]}
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

  it("names a world actor with their role and shows their action as an event", async () => {
    render(
      <ChatTranscriptPane
        turns={[
          { role: "customer", text: "Can I get a discount?" },
          {
            role: "world_actor",
            speakerName: "Pat Lee",
            actorRole: "store manager",
            text: 'Refused · crm.approve_discount\n→ args: {"percent":20}',
          },
          { role: "world_actor", text: "crm.reject_discount" },
        ]}
      />,
    );

    // On their own turns only: acting in the world doesn't put them on the call.
    expect(screen.getByText("Pat Lee · store manager")).toBeInTheDocument();
    expect(screen.getByText("Someone")).toBeInTheDocument();
    expect(screen.queryByText("On the call:")).toBeNull();
    expect(
      screen.getByText("Refused · crm.approve_discount"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/args:/)).toBeNull();
    expect(screen.queryByText(/world_actor|in the world/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^Details/ }));
    expect(screen.getByText(/args:/)).toBeInTheDocument();
  });

  it("opens a world action's fold when a search matched inside it", async () => {
    render(
      <ChatTranscriptPane
        turns={[
          {
            role: "world_actor",
            speakerName: "Pat Lee",
            text: 'crm.approve_discount\n→ args: {"percent":20}',
          },
        ]}
      />,
    );
    await userEvent.type(
      screen.getByPlaceholderText("Search transcript"),
      "percent",
    );
    expect(screen.getByText(/args:/)).toBeInTheDocument();
  });
});

describe("ChatTranscriptPane tool and hand-off rows", () => {
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
    const tool = screen.getByText(/· Tool call$/).parentElement;
    const customer = screen.getByText(/· Customer$/).parentElement;
    expect(getComputedStyle(tool).borderLeftColor).not.toBe(
      getComputedStyle(customer).borderLeftColor,
    );
  });
});
