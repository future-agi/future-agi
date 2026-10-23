import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ChatTranscriptPane from "../ChatTranscriptPane";

describe("ChatTranscriptPane", () => {
  it("names a second participant as someone who is not the customer", () => {
    render(
      <ChatTranscriptPane
        turns={[
          { role: "customer", text: "One item please" },
          { role: "sub_actor", speakerName: "Rowan", text: "Make it two" },
        ]}
      />,
    );
    expect(screen.getByText(/Rowan \(not the customer\)/)).toBeInTheDocument();
    expect(screen.queryByText(/sub_actor/)).toBeNull();
  });
});
