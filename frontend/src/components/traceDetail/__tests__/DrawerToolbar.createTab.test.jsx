import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isEE: false }),
}));

import DrawerToolbar from "../DrawerToolbar";

const TABS = [
  { id: "trace", label: "Trace", icon: "solar:link-linear", isDefault: true },
];

// Filter, Display and Imagine are left out, so the only possible button is
// the "+" create-view button.
const renderToolbar = (props) =>
  render(
    <DrawerToolbar
      tabs={TABS}
      activeTabId="trace"
      onTabChange={vi.fn()}
      hideFilter
      hideDisplay
      {...props}
    />,
  );

describe("DrawerToolbar create-view button", () => {
  // The trace drawer passes no create handler: the "+" stays visible but
  // disabled, says why, and has no click path.
  it("renders a disabled '+' with the reason when the drawer passes no create handler", () => {
    renderToolbar();

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    const plus = screen.getByRole("button", { name: "Create new view" });
    expect(plus).toHaveAttribute("aria-disabled", "true");
    expect(plus).toHaveAttribute("title", "Not available in this release");

    // Clicking or pressing Enter does nothing and throws nothing.
    fireEvent.click(plus);
    fireEvent.keyDown(plus, { key: "Enter" });
    expect(plus).toHaveAttribute("aria-disabled", "true");
  });

  it("keeps the working '+' free of the disabled state", () => {
    renderToolbar({ onCreateTab: vi.fn() });

    const plus = screen.getByRole("button", { name: "Create new view" });
    expect(plus).not.toHaveAttribute("aria-disabled");
  });

  it("renders a labelled '+' that calls the create handler", () => {
    const onCreateTab = vi.fn();
    renderToolbar({ onCreateTab });

    fireEvent.click(screen.getByRole("button", { name: "Create new view" }));

    expect(onCreateTab).toHaveBeenCalledTimes(1);
  });

  it("renders no '+' when read-only, even with a create handler", () => {
    renderToolbar({ onCreateTab: vi.fn(), readOnly: true });

    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
