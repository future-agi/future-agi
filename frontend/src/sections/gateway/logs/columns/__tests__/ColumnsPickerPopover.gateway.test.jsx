import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import ColumnsPickerPopover from "../ColumnsPickerPopover";
import {
  DEFAULT_CONFIG,
  resolveColumns,
  resolveGatewayColumns,
} from "../columnModel";

function buildProps(config = DEFAULT_CONFIG) {
  const resolved = resolveColumns({
    config,
    declarations: [{ name: "tenant" }],
    status: "success",
  });
  const gateway = resolveGatewayColumns(config);
  return {
    anchorEl: document.body,
    open: true,
    onClose: vi.fn(),
    entries: { ...resolved.entries, gateway: gateway.entries },
    stale: resolved.stale,
    visibleCount: resolved.visibleCount + gateway.visibleCount,
    totalCount: resolved.totalCount + gateway.totalCount,
    declarationStatus: "success",
    declarationsAvailable: true,
    atCap: resolved.atCap,
    onToggle: vi.fn(),
    onMove: vi.fn(),
    onRemove: vi.fn(),
    onReset: vi.fn(),
    onRetryDeclarations: vi.fn(),
  };
}

describe("ColumnsPickerPopover Agent & billing group", () => {
  it("lists the eight columns under their own heading, unchecked by default", () => {
    render(<ColumnsPickerPopover {...buildProps()} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(within(dialog).getByText("Agent & billing")).toBeInTheDocument();
    for (const label of [
      "Billing mode",
      "Credential type",
      "Upstream cost",
      "Markup",
      "Attempts",
      "Agent",
      "Step",
      "Run",
    ]) {
      expect(
        within(dialog).getByRole("checkbox", { name: label }),
      ).not.toBeChecked();
    }
    expect(within(dialog).getByText("10 of 19 visible")).toBeInTheDocument();
  });

  it("toggles a gateway column by its id", async () => {
    const user = userEvent.setup();
    const props = buildProps();
    render(<ColumnsPickerPopover {...props} />);
    await user.click(screen.getByRole("checkbox", { name: "Run" }));
    expect(props.onToggle).toHaveBeenCalledWith("gateway:agentRunId");
  });

  it("moves a saved gateway column within its group", async () => {
    const user = userEvent.setup();
    const props = buildProps({
      v: 1,
      columns: [
        ...DEFAULT_CONFIG.columns,
        { id: "gateway:billingMode" },
        { id: "gateway:agentStep" },
      ],
      hidden: [],
    });
    render(<ColumnsPickerPopover {...props} />);
    expect(screen.getByRole("checkbox", { name: "Step" })).toBeChecked();
    expect(
      screen.getByRole("button", { name: "Move Billing mode up" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move Step up" }));
    expect(props.onMove).toHaveBeenCalledWith("gateway:agentStep", -1);
  });

  it("filters the group by the search and reports no matches across groups", async () => {
    const user = userEvent.setup();
    render(<ColumnsPickerPopover {...buildProps()} />);
    const search = screen.getByRole("textbox", { name: "Search columns" });

    await user.type(search, "cred");
    expect(screen.getByText("Agent & billing")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Credential type" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Run" })).toBeNull();

    await user.clear(search);
    await user.type(search, "zzz");
    expect(screen.queryByText("Agent & billing")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("No matching columns");
  });

  it("renders no group when the entries carry none", () => {
    const props = buildProps();
    delete props.entries.gateway;
    render(<ColumnsPickerPopover {...props} />);
    expect(screen.queryByText("Agent & billing")).toBeNull();
  });
});
