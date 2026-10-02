import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import ColumnsPickerPopover from "../ColumnsPickerPopover";
import { DEFAULT_CONFIG, resolveColumns } from "../columnModel";

function buildProps(overrides = {}) {
  const resolved = resolveColumns({
    config: overrides.config || {
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: [],
    },
    declarations: overrides.declarations || [
      { name: "tenant" },
      { name: "feature" },
    ],
    status: overrides.status || "success",
  });
  return {
    anchorEl: document.body,
    open: true,
    onClose: vi.fn(),
    entries: resolved.entries,
    stale: resolved.stale,
    visibleCount: resolved.visibleCount,
    totalCount: resolved.totalCount,
    declarationStatus: overrides.status || "success",
    unavailableCustomCount: overrides.unavailableCustomCount || 0,
    onToggle: vi.fn(),
    onMove: vi.fn(),
    onRemove: vi.fn(),
    onReset: vi.fn(),
    onRetryDeclarations: vi.fn(),
  };
}

describe("ColumnsPickerPopover", () => {
  it("is a named dialog that lists groups, counts and a locked Timestamp (AC5)", () => {
    const props = buildProps();
    render(<ColumnsPickerPopover {...props} />);

    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(within(dialog).getByText("11 of 12 visible")).toBeInTheDocument();
    expect(within(dialog).getByText("Built-in")).toBeInTheDocument();
    expect(within(dialog).getByText("Custom properties")).toBeInTheDocument();

    const timestamp = within(dialog).getByRole("checkbox", {
      name: "Timestamp (always visible)",
    });
    expect(timestamp).toBeChecked();
    expect(timestamp).toBeDisabled();
    expect(
      within(dialog).queryByRole("button", { name: "Move Timestamp up" }),
    ).toBeNull();
    expect(
      within(dialog).getByRole("button", { name: "Move Model up" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Move Model down" }),
    ).toBeEnabled();
    expect(
      within(dialog).getByRole("button", { name: "Move Session ID down" }),
    ).toBeDisabled();
    // Search appears only above 8 entries (D6).
    expect(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
    ).toBeInTheDocument();
  });

  it("toggles, moves and resets through keyboard-operable controls (AC5/AC17)", async () => {
    const user = userEvent.setup();
    const props = buildProps();
    render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });

    const provider = within(dialog).getByRole("checkbox", { name: "Provider" });
    provider.focus();
    await user.keyboard(" ");
    expect(props.onToggle).toHaveBeenCalledWith("builtin:provider");

    const moveDown = within(dialog).getByRole("button", {
      name: "Move Model down",
    });
    moveDown.focus();
    await user.keyboard("{Enter}");
    expect(props.onMove).toHaveBeenCalledWith("builtin:model", 1);

    await user.click(within(dialog).getByRole("checkbox", { name: "feature" }));
    expect(props.onToggle).toHaveBeenCalledWith("metadata:feature");
    expect(
      within(dialog).getByRole("button", { name: "Move feature up" }),
    ).toBeDisabled();

    await user.click(
      within(dialog).getByRole("button", { name: "Reset to default" }),
    );
    expect(props.onReset).toHaveBeenCalledTimes(1);

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("filters entries by search text without touching the data", async () => {
    const user = userEvent.setup();
    render(<ColumnsPickerPopover {...buildProps()} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    await user.type(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
      "ten",
    );
    expect(
      within(dialog).getByRole("checkbox", { name: "tenant" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", { name: "Provider" }),
    ).toBeNull();
  });

  it("shows the stale group with a Remove action after a successful fetch (AC14)", async () => {
    const user = userEvent.setup();
    const props = buildProps({ declarations: [{ name: "feature" }] });
    render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(within(dialog).getByText("No longer declared")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", { name: "tenant" }),
    ).toBeNull();
    await user.click(
      within(dialog).getByRole("button", { name: "Remove tenant" }),
    );
    expect(props.onRemove).toHaveBeenCalledWith("metadata:tenant");
  });

  it("shows an error row with an accessible Retry and no stored names while declarations fail (AC18/J5)", async () => {
    const user = userEvent.setup();
    const props = buildProps({ status: "error", unavailableCustomCount: 1 });
    render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/Couldn't load custom properties/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/1 saved custom column unavailable/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("tenant")).toBeNull();
    expect(within(dialog).queryByText("No longer declared")).toBeNull();
    await user.click(
      within(dialog).getByRole("button", {
        name: "Retry loading custom properties",
      }),
    );
    expect(props.onRetryDeclarations).toHaveBeenCalledTimes(1);
  });

  it("shows a loading row while declarations are pending", () => {
    render(<ColumnsPickerPopover {...buildProps({ status: "pending" })} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/Loading custom properties/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("tenant")).toBeNull();
  });

  it("points to Custom Properties when nothing is declared (R36)", () => {
    render(
      <ColumnsPickerPopover
        {...buildProps({ config: DEFAULT_CONFIG, declarations: [] })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/No custom properties declared/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", {
        name: "Declare one in Custom Properties",
      }),
    ).toHaveAttribute("href", "/dashboard/gateway/custom-properties");
    // Ten built-ins already exceed the eight-entry threshold, so search stays (D6).
    expect(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
    ).toBeInTheDocument();
  });
});
