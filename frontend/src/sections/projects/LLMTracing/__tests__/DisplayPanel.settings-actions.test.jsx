import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import DisplayPanel from "../DisplayPanel";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const baseProps = {
  anchorEl: document.createElement("button"),
  open: true,
  onClose: vi.fn(),
  cellHeight: "Short",
};

const REASON = "Not available in this release";

describe("Display settings only advertise implemented shared defaults", () => {
  // Sessions and Users have no shared default: the action stays visible but
  // disabled, says why, and has no click path.
  it.each(["sessions", "users"])(
    "shows the unsupported shared default disabled with the reason for %s",
    (mode) => {
      const onResetView = vi.fn();
      const onClose = vi.fn();
      render(
        <DisplayPanel
          {...baseProps}
          mode={mode}
          onClose={onClose}
          onResetView={onResetView}
        />,
      );
      const setDefault = screen.getByRole("button", {
        name: "Set default for everyone",
      });
      expect(setDefault).toHaveAttribute("aria-disabled", "true");
      expect(setDefault).toHaveAttribute("title", REASON);
      fireEvent.click(setDefault);
      expect(onClose).not.toHaveBeenCalled();

      fireEvent.click(screen.getByText("Reset"));
      expect(onResetView).toHaveBeenCalledOnce();
    },
  );

  // Compare graph draws nothing on Sessions: the box stays visible,
  // unchecked and disabled, and toggles nothing. (Project Users is covered
  // in ObserveToolbar.compare-capabilities, which renders the project route;
  // without an observeId this panel is the standalone users page, which
  // shows no GRAPH section, as on production.)
  it("shows Compare graph disabled and unchecked with the reason for sessions", () => {
    const onCompareToggle = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        mode="sessions"
        onCompareToggle={onCompareToggle}
        isCompareActive
      />,
    );
    const compare = screen.getByRole("checkbox", { name: "Compare graph" });
    expect(compare).toHaveAttribute("aria-disabled", "true");
    expect(compare).not.toBeChecked();
    expect(
      screen.getByText("Compare graph").closest(`[title="${REASON}"]`),
    ).not.toBeNull();

    fireEvent.click(compare);

    expect(onCompareToggle).not.toHaveBeenCalled();
    expect(compare).not.toBeChecked();
  });

  it("keeps the working Compare graph and no disabled default on traces", () => {
    const onCompareToggle = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        mode="traces"
        onCompareToggle={onCompareToggle}
      />,
    );
    const compare = screen.getByRole("checkbox", { name: "Compare graph" });
    expect(compare).not.toHaveAttribute("aria-disabled");
    fireEvent.click(compare);
    expect(onCompareToggle).toHaveBeenCalledOnce();
    expect(
      screen.queryByText("Set default for everyone"),
    ).not.toBeInTheDocument();
  });
  it("retains the shared-default action when the caller implements it", () => {
    const onSetDefaultView = vi.fn();
    render(<DisplayPanel {...baseProps} onSetDefaultView={onSetDefaultView} />);
    fireEvent.click(screen.getByText("Set default for everyone"));
    expect(onSetDefaultView).toHaveBeenCalledOnce();
  });
});
