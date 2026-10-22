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
