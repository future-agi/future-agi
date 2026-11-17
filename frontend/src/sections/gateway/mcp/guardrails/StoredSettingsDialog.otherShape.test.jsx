import React from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import StoredSettingsDialog from "./StoredSettingsDialog";

// The API stores any JSON as tool_rate_limits, so a stored value can be
// something other than an object. The dialog shows it as stored, read-only,
// says so, and an added row replaces it with the rows.

const UNREADABLE =
  "The stored per-tool limits are not in a form this page can edit. Adding a limit here replaces them.";

const limitsSection = () =>
  screen.getByRole("region", { name: "Stored per-tool limits" });

function renderDialog(stored) {
  const onSave = vi.fn();
  render(
    <StoredSettingsDialog
      stored={stored}
      canEdit
      onClose={() => {}}
      onSave={onSave}
    />,
  );
  return onSave;
}

describe("Stored per-tool limits of another shape", () => {
  it.each([
    ["a list", ["crm__search", 5], '["crm__search",5]'],
    ["a string", "crm__search=5", '"crm__search=5"'],
  ])("shows %s as stored, read-only, and says so", (_, value, shown) => {
    renderDialog({ enabled: true, tool_rate_limits: value });
    const section = limitsSection();
    expect(within(section).getByText(shown)).toBeInTheDocument();
    expect(within(section).getByText(UNREADABLE)).toBeInTheDocument();
    expect(
      within(section).queryByText("No per-tool limits stored."),
    ).toBeNull();
    expect(within(section).queryAllByRole("listitem")).toEqual([]);
  });

  it("replaces the stored value with the rows once a limit is added", async () => {
    const user = userEvent.setup();
    const onSave = renderDialog({
      enabled: true,
      tool_rate_limits: ["crm__search", 5],
    });
    const section = limitsSection();
    await user.type(within(section).getByLabelText("Tool name"), "crm__search");
    await user.type(within(section).getByLabelText("Per minute"), "7");
    await user.click(
      within(section).getByRole("button", { name: "Add limit" }),
    );
    expect(within(section).queryByText(UNREADABLE)).toBeNull();
    expect(
      within(section)
        .getAllByRole("listitem")
        .map((row) => row.textContent),
    ).toEqual(["crm__search7 per minute"]);
    await user.click(
      screen.getByRole("button", { name: "Save stored settings" }),
    );
    expect(onSave).toHaveBeenCalledExactlyOnceWith({
      enabled: true,
      tool_rate_limits: { crm__search: 7 },
    });
  });

  it("still says no limits are stored when there are none", () => {
    renderDialog({ enabled: true });
    expect(
      within(limitsSection()).getByText("No per-tool limits stored."),
    ).toBeInTheDocument();
    expect(screen.queryByText(UNREADABLE)).toBeNull();
  });
});
