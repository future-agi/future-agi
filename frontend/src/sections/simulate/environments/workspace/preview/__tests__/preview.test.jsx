import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import DroppedCases from "../DroppedCases";

describe("Feature 3 preview", () => {
  it("lists dropped cases with why and restored ones with whether proved again", () => {
    render(
      <DroppedCases
        dropped={[
          {
            key: "a",
            name: "Refund",
            reason: "The agent lost refund",
            agent_version: "v3",
          },
        ]}
        restored={[{ key: "b", name: "Cancel", proved: false }]}
      />,
    );
    expect(
      screen.getByText("Refund: The agent lost refund (agent v3)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Cancel: Not proved again yet"),
    ).toBeInTheDocument();
  });
});
