import React from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import StoredSettingsDialog from "./StoredSettingsDialog";

// Restore (v1.58.0 mcp/MCPGuardrailsTab.jsx): per-tool rate limits are rows
// a person adds and removes, saved as `tool_rate_limits`, and Allowed
// servers offers the organization's connected servers from the MCP status.

const save = () => screen.getByRole("button", { name: "Save stored settings" });
// A button element is disabled; a clickable chip says so with aria-disabled.
const isDisabled = (element) =>
  element.disabled === true || element.getAttribute("aria-disabled") === "true";
const limitsSection = () =>
  screen.getByRole("region", { name: "Stored per-tool limits" });

function renderDialog(props = {}) {
  const onSave = vi.fn();
  render(
    <StoredSettingsDialog
      stored={{ enabled: true }}
      canEdit
      onClose={() => {}}
      onSave={onSave}
      {...props}
    />,
  );
  return onSave;
}

async function addLimit(user, tool, limit) {
  const section = limitsSection();
  await user.clear(within(section).getByLabelText("Tool name"));
  if (tool) await user.type(within(section).getByLabelText("Tool name"), tool);
  await user.clear(within(section).getByLabelText("Per minute"));
  if (limit)
    await user.type(within(section).getByLabelText("Per minute"), limit);
  await user.click(within(section).getByRole("button", { name: "Add limit" }));
}

describe("Stored per-tool limits as rows", () => {
  it("lists each stored limit as a row", () => {
    renderDialog({
      stored: { tool_rate_limits: { crm__search: 5, crm__write: 1 } },
    });
    const rows = within(limitsSection()).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "crm__search5 per minute",
      "crm__write1 per minute",
    ]);
  });

  it("adds and removes rows and saves them as tool_rate_limits", async () => {
    const user = userEvent.setup();
    const onSave = renderDialog({
      stored: { enabled: true, tool_rate_limits: { crm__search: 5 } },
    });
    await addLimit(user, "crm__write", "2");
    await addLimit(user, "support__ticket", "30");
    await user.click(
      screen.getByRole("button", { name: "Remove the limit for crm__search" }),
    );
    expect(
      within(limitsSection())
        .getAllByRole("listitem")
        .map((row) => row.textContent),
    ).toEqual(["crm__write2 per minute", "support__ticket30 per minute"]);
    await user.click(save());
    expect(onSave).toHaveBeenCalledExactlyOnceWith({
      enabled: true,
      tool_rate_limits: { crm__write: 2, support__ticket: 30 },
    });
  });

  it("gives a tool that already has a limit the new one", async () => {
    const user = userEvent.setup();
    const onSave = renderDialog({
      stored: { tool_rate_limits: { crm__search: 5 } },
    });
    await addLimit(user, " crm__search ", "12");
    await user.click(save());
    expect(onSave).toHaveBeenCalledExactlyOnceWith({
      tool_rate_limits: { crm__search: 12 },
    });
  });

  it("saves an empty set after the last row is removed", async () => {
    const user = userEvent.setup();
    const onSave = renderDialog({
      stored: { tool_rate_limits: { crm__search: 5 } },
    });
    await user.click(
      screen.getByRole("button", { name: "Remove the limit for crm__search" }),
    );
    expect(
      within(limitsSection()).getByText("No per-tool limits stored."),
    ).toBeInTheDocument();
    await user.click(save());
    expect(onSave).toHaveBeenCalledExactlyOnceWith({ tool_rate_limits: {} });
  });

  it.each(["0", "-1", "1.2", "ten"])(
    "does not add a limit of %s",
    async (limit) => {
      const user = userEvent.setup();
      const onSave = renderDialog();
      await addLimit(user, "crm__search", limit);
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Enter a whole number greater than zero.",
      );
      expect(within(limitsSection()).queryAllByRole("listitem")).toEqual([]);
      // A typed limit that was not added holds the save.
      expect(save()).toBeDisabled();
      await user.clear(within(limitsSection()).getByLabelText("Per minute"));
      await user.clear(within(limitsSection()).getByLabelText("Tool name"));
      expect(save()).toBeEnabled();
      await user.click(save());
      expect(onSave).toHaveBeenCalledExactlyOnceWith({ enabled: true });
    },
  );

  it("asks for the tool's name before adding a limit", async () => {
    const user = userEvent.setup();
    renderDialog();
    await addLimit(user, "", "10");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter the tool's name.",
    );
    expect(within(limitsSection()).queryAllByRole("listitem")).toEqual([]);
    expect(save()).toBeDisabled();
  });

  it("keeps a stored limits value of another shape when no row changes", async () => {
    const user = userEvent.setup();
    const stored = { enabled: true, tool_rate_limits: "legacy" };
    const onSave = renderDialog({ stored });
    await user.click(
      screen.getByRole("checkbox", { name: "Validate tool inputs" }),
    );
    await user.click(save());
    expect(onSave).toHaveBeenCalledExactlyOnceWith({
      ...stored,
      validate_inputs: true,
    });
  });
});

describe("Allowed servers suggestions", () => {
  it("offers the connected servers not yet allowed, and adds one on click", async () => {
    const user = userEvent.setup();
    const onSave = renderDialog({
      stored: { allowed_servers: ["crm"] },
      connectedServers: ["crm", "support", "github", "support"],
    });
    expect(
      screen.queryByRole("button", { name: "Add crm to Allowed servers" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Add github to Allowed servers" }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Add support to Allowed servers" }),
    );
    expect(screen.getByLabelText("Allowed servers")).toHaveValue(
      "crm\nsupport",
    );
    expect(
      screen.queryByRole("button", { name: "Add support to Allowed servers" }),
    ).toBeNull();
    await user.click(save());
    expect(onSave).toHaveBeenCalledExactlyOnceWith({
      allowed_servers: ["crm", "support"],
    });
  });

  it("offers nothing when no server is connected", () => {
    renderDialog({ connectedServers: [] });
    expect(screen.queryByText("Connected servers:")).toBeNull();
  });
});

describe("Locked", () => {
  it.each([
    { canEdit: false, busy: false },
    { canEdit: true, busy: true },
  ])("disables the rows, the new limit and the suggestions: %s", (props) => {
    renderDialog({
      stored: { tool_rate_limits: { crm__search: 5 } },
      connectedServers: ["crm"],
      ...props,
    });
    for (const name of [
      "Remove the limit for crm__search",
      "Add limit",
      "Add crm to Allowed servers",
    ]) {
      expect(isDisabled(screen.getByRole("button", { name }))).toBe(true);
    }
    expect(screen.getByLabelText("Tool name")).toBeDisabled();
    expect(screen.getByLabelText("Per minute")).toBeDisabled();
  });
});
