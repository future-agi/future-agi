import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ChatTranscriptPane from "../ChatTranscriptPane";

describe("ChatTranscriptPane", () => {
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
