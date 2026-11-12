import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import GatewayDetailDrawer from "./GatewayDetailDrawer";

function drawers(secondOpen) {
  return (
    <>
      <GatewayDetailDrawer open onClose={vi.fn()} title="First detail">
        <input aria-label="Detail draft" />
      </GatewayDetailDrawer>
      <GatewayDetailDrawer
        open={secondOpen}
        onClose={vi.fn()}
        title="Second detail"
      >
        <p>Nested detail content</p>
      </GatewayDetailDrawer>
    </>
  );
}

describe("console-v2 detail drawer coverage", () => {
  it("covers the older drawer without discarding its state", async () => {
    const user = userEvent.setup();
    const view = render(drawers(false));
    const draft = screen.getByRole("textbox", { name: "Detail draft" });
    // R-LIT: literal, visibly synthetic UI-state fixture; not a credential.
    await user.type(draft, "draft preserved");

    view.rerender(drawers(true));
    await waitFor(() => expect(draft).not.toBeVisible());
    expect(screen.getByText("Nested detail content")).toBeVisible();
    expect(draft).toBeInTheDocument();

    view.rerender(drawers(false));
    await waitFor(() => expect(draft).toBeVisible());
    expect(draft).toHaveValue("draft preserved");
  });
});
