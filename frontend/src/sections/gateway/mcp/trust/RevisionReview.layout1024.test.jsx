import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import axios from "src/utils/axios";
import { renderMcp } from "../testing/renderMcp";
import RevisionReview from "./RevisionReview";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { gateway: {} },
}));

// Every MCP tab starts at the page frame's 12px gap under the tab bar (screen
// pass F09): the Tool revisions tab adds no top padding of its own.
describe("Tool revisions tab spacing", () => {
  it("starts at the frame gap, with no top padding of its own", () => {
    // The lists stay loading: this checks the tab's frame, not its rows.
    vi.mocked(axios.get).mockReturnValue(new Promise(() => {}));
    const { container } = renderMcp(
      <RevisionReview gatewayId="gw-1" organizationId="org-a" canEdit />,
    );
    expect(
      screen.getByRole("heading", { name: "Tool revisions" }),
    ).toBeInTheDocument();
    expect(container.firstElementChild).not.toHaveStyle({
      paddingTop: "24px",
    });
  });
});
