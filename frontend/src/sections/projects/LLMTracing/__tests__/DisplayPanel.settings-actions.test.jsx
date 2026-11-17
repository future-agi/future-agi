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
// The action shares the view as a project tab; nothing loads it for anyone,
// so its label never calls it a default.
const SHARE_LABEL = "Share with project";

describe("Display settings only advertise implemented shared defaults", () => {
  // A Sessions or Users view that passes neither a share handler nor a
  // reason: the action stays visible but disabled, says the release line,
  // and has no click path.
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
      const setDefault = screen.getByRole("button", { name: SHARE_LABEL });
      expect(setDefault).toHaveAttribute("aria-disabled", "true");
      expect(setDefault).toHaveAttribute("title", REASON);
      fireEvent.click(setDefault);
      expect(onClose).not.toHaveBeenCalled();

      fireEvent.click(screen.getByText("Reset"));
      expect(onResetView).toHaveBeenCalledOnce();
    },
  );

  // A view that cannot share (Sessions on a user's page: no project to
  // share with) passes its reason, which replaces the release line.
  it("shows the view's own reason on a shared default it cannot offer", () => {
    const reason =
      "Shared views need a project. Open the sessions from their project.";
    const onClose = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        mode="sessions"
        onClose={onClose}
        setDefaultDisabledReason={reason}
      />,
    );
    const setDefault = screen.getByRole("button", { name: SHARE_LABEL });
    expect(setDefault).toHaveAttribute("aria-disabled", "true");
    expect(setDefault).toHaveAttribute("title", reason);
    fireEvent.click(setDefault);
    expect(onClose).not.toHaveBeenCalled();
  });

  // The working action, on every view that passes a handler, is labelled
  // "Share with project" and never "Set default for everyone".
  it.each(["traces", "sessions", "users"])(
    "labels the working action Share with project on %s",
    (mode) => {
      const onSetDefaultView = vi.fn();
      const onClose = vi.fn();
      render(
        <DisplayPanel
          {...baseProps}
          mode={mode}
          onClose={onClose}
          onSetDefaultView={onSetDefaultView}
        />,
      );
      expect(screen.queryByText(/default for everyone/i)).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: SHARE_LABEL }));
      expect(onSetDefaultView).toHaveBeenCalledOnce();
      expect(onClose).toHaveBeenCalledOnce();
    },
  );

  // A caller whose action saves without sharing passes its own label, so
  // the action never claims to share. (No view does this today.)
  it("labels the action with the view's own label when it does not share", () => {
    const onSetDefaultView = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        mode="traces"
        onSetDefaultView={onSetDefaultView}
        setDefaultLabel="Save as personal view"
      />,
    );
    expect(screen.queryByText(SHARE_LABEL)).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Save as personal view" }),
    );
    expect(onSetDefaultView).toHaveBeenCalledOnce();
  });

  // Traces on a user's page cannot share (no project; its saved views live
  // in the page's tab bar): it passes a reason, and the action is listed but
  // disabled with that reason, as for Sessions there.
  it("shows Share with project disabled with the reason on Traces when it cannot share", () => {
    const reason =
      "Shared views need a project. Save views on a user's page from its tab bar.";
    const onClose = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        mode="traces"
        onClose={onClose}
        setDefaultDisabledReason={reason}
      />,
    );
    const share = screen.getByRole("button", { name: SHARE_LABEL });
    expect(share).toHaveAttribute("aria-disabled", "true");
    expect(share).toHaveAttribute("title", reason);
    fireEvent.click(share);
    expect(onClose).not.toHaveBeenCalled();
  });

  // Sessions draws a Compare graph: the box is enabled, shows the view's
  // state and calls its toggle. Project Users is covered in
  // ObserveToolbar.compare-capabilities, which renders the project route;
  // without an observeId this panel is the standalone users page, which
  // shows no GRAPH section, as on production.
  it("shows a working, checked Compare graph for sessions", () => {
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
    expect(compare).not.toHaveAttribute("aria-disabled");
    expect(compare).toBeChecked();
    expect(
      screen.getByText("Compare graph").closest(`[title="${REASON}"]`),
    ).toBeNull();

    fireEvent.click(compare);

    expect(onCompareToggle).toHaveBeenCalledOnce();
  });

  // A view that draws no graphs (Sessions on a user's page) keeps the box
  // listed, unchecked and disabled with its own reason; it toggles nothing.
  it("shows Compare graph disabled with the view's reason when it draws no graphs", () => {
    const reason =
      "Compare graph needs a project. Open the sessions from their project.";
    const onCompareToggle = vi.fn();
    render(
      <DisplayPanel
        {...baseProps}
        mode="sessions"
        onCompareToggle={onCompareToggle}
        isCompareActive
        compareDisabledReason={reason}
      />,
    );
    const compare = screen.getByRole("checkbox", { name: "Compare graph" });
    expect(compare).toHaveAttribute("aria-disabled", "true");
    expect(compare).not.toBeChecked();
    expect(
      screen.getByText("Compare graph").closest(`[title="${reason}"]`),
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
    expect(screen.queryByText(SHARE_LABEL)).not.toBeInTheDocument();
  });
  it("retains the shared-default action when the caller implements it", () => {
    const onSetDefaultView = vi.fn();
    render(<DisplayPanel {...baseProps} onSetDefaultView={onSetDefaultView} />);
    fireEvent.click(screen.getByText(SHARE_LABEL));
    expect(onSetDefaultView).toHaveBeenCalledOnce();
  });
});
