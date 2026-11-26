import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";

import { render } from "src/utils/test-utils";
import SideDrawer from "../SideDrawer";

describe("SideDrawer", () => {
  it("shows its children when open", () => {
    render(
      <SideDrawer open onClose={() => {}}>
        <p>Manage versions</p>
      </SideDrawer>,
    );
    expect(screen.getByText("Manage versions")).toBeInTheDocument();
  });

  it("does not render its children when closed", () => {
    render(
      <SideDrawer open={false} onClose={() => {}}>
        <p>Manage versions</p>
      </SideDrawer>,
    );
    expect(screen.queryByText("Manage versions")).not.toBeInTheDocument();
  });

  it.each([
    [false, 0],
    [true, 1],
  ])("leaves the page usable only when beside it (%s)", (beside, reachable) => {
    render(
      <>
        <button type="button">Chat</button>
        <SideDrawer open beside={beside} onClose={() => {}}>
          <p>Review changes</p>
        </SideDrawer>
      </>,
    );
    expect(screen.getByText("Review changes")).toBeVisible();
    expect(screen.queryAllByRole("button", { name: "Chat" })).toHaveLength(
      reachable,
    );
  });

  // Beside, nothing keeps focus in the drawer: a button that disables itself when pressed drops it to the page.
  it("closes on Escape beside the page, also once focus has dropped out of it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <>
        <input aria-label="Chat" />
        <SideDrawer open beside onClose={onClose}>
          <p>Review changes</p>
        </SideDrawer>
      </>,
    );
    document.activeElement.blur();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    // Typing in the page isn't about the drawer.
    await user.click(screen.getByLabelText("Chat"));
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("exposes an accessible close button that fires onClose", async () => {
    const onClose = vi.fn();
    render(
      <SideDrawer open onClose={onClose}>
        <p>Manage versions</p>
      </SideDrawer>,
    );
    const close = screen.getByRole("button", { name: /close/i });
    await userEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
