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

  it("names a world actor's turn as theirs, with an unnamed one read as someone", () => {
    render(
      <ChatTranscriptPane
        turns={[
          { role: "customer", text: "Can I get a discount?" },
          {
            role: "world_actor",
            speakerName: "Pat Lee",
            text: "Action · crm.approve_discount",
          },
          { role: "world_actor", text: "Action · crm.reject_discount" },
        ]}
      />,
    );

    expect(screen.getByText(/Pat Lee \(in the world\)/)).toBeInTheDocument();
    expect(screen.getByText(/Someone \(in the world\)/)).toBeInTheDocument();
    expect(screen.queryByText(/world_actor/)).toBeNull();
  });
});
