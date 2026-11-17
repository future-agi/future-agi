import React from "react";
import { expect, it, vi } from "vitest";
import { fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import StoredSettingsDialog from "./StoredSettingsDialog";

it("edits every v1 stored field without dropping unknown settings", async () => {
  const onSave = vi.fn();
  const stored = { enabled: false, extension: { keep: true } };
  const user = userEvent.setup();
  render(
    <StoredSettingsDialog
      stored={stored}
      canEdit
      onClose={() => {}}
      onSave={onSave}
    />,
  );
  expect(
    screen.getByText(
      "Saved MCP guardrail settings are stored for your organization. They are not active on this gateway.",
    ),
  ).toBeVisible();
  for (const label of [
    "Enabled in stored settings",
    "Validate tool inputs",
    "Validate tool outputs",
  ]) {
    await user.click(screen.getByRole("checkbox", { name: label }));
  }
  await user.type(screen.getByLabelText("Blocked tools"), "shell{Enter}write");
  await user.type(
    screen.getByLabelText("Allowed servers"),
    "crm{Enter}support",
  );
  await user.type(screen.getByLabelText("Tool name"), "crm__search");
  await user.type(screen.getByLabelText("Per minute"), "10");
  await user.click(screen.getByRole("button", { name: "Add limit" }));
  await user.click(
    screen.getByRole("button", { name: "Save stored settings" }),
  );
  expect(onSave).toHaveBeenCalledExactlyOnceWith({
    ...stored,
    enabled: true,
    validate_inputs: true,
    validate_outputs: true,
    blocked_tools: ["shell", "write"],
    allowed_servers: ["crm", "support"],
    tool_rate_limits: { crm__search: 10 },
  });
  expect(stored).toEqual({ enabled: false, extension: { keep: true } });
});

it.each(["0", "-1", "1.2", "10.0", "ten"])(
  "does not add or save a limit of %s",
  (value) => {
    const onSave = vi.fn();
    render(
      <StoredSettingsDialog
        stored={{ enabled: false }}
        canEdit
        onClose={() => {}}
        onSave={onSave}
      />,
    );
    fireEvent.change(screen.getByLabelText("Tool name"), {
      target: { value: "tool" },
    });
    fireEvent.change(screen.getByLabelText("Per minute"), {
      target: { value },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add limit" }));
    expect(screen.queryAllByRole("listitem")).toEqual([]);
    expect(
      screen.getByRole("button", { name: "Save stored settings" }),
    ).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  },
);

it("allows a valid limit after correcting a zero", () => {
  const onSave = vi.fn();
  render(
    <StoredSettingsDialog
      stored={{ enabled: false }}
      canEdit
      onClose={() => {}}
      onSave={onSave}
    />,
  );
  const limit = screen.getByLabelText("Per minute");
  const add = screen.getByRole("button", { name: "Add limit" });
  const save = screen.getByRole("button", { name: "Save stored settings" });
  fireEvent.change(screen.getByLabelText("Tool name"), {
    target: { value: "tool" },
  });
  fireEvent.change(limit, { target: { value: "0" } });
  fireEvent.click(add);
  expect(save).toBeDisabled();
  expect(
    screen.getByText("Enter a whole number greater than zero."),
  ).toBeInTheDocument();
  fireEvent.change(limit, { target: { value: "1" } });
  fireEvent.click(add);
  expect(save).toBeEnabled();
  fireEvent.click(save);
  expect(onSave).toHaveBeenCalledExactlyOnceWith({
    enabled: false,
    tool_rate_limits: { tool: 1 },
  });
});

it.each([
  { canEdit: false, busy: false },
  { canEdit: true, busy: true },
])("disables edits and save when locked: %s", (props) => {
  const onSave = vi.fn();
  render(
    <StoredSettingsDialog
      stored={{ enabled: true }}
      {...props}
      onClose={() => {}}
      onSave={onSave}
    />,
  );
  for (const input of screen.getAllByRole("checkbox"))
    expect(input).toBeDisabled();
  for (const input of screen.getAllByRole("textbox"))
    expect(input).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Save stored settings" }),
  ).toBeDisabled();
});

it("cancels without saving and leaves the source unchanged", async () => {
  const onClose = vi.fn();
  const onSave = vi.fn();
  const stored = { enabled: true };
  render(
    <StoredSettingsDialog
      stored={stored}
      canEdit
      onClose={onClose}
      onSave={onSave}
    />,
  );
  await userEvent.click(
    screen.getByRole("checkbox", { name: "Enabled in stored settings" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
  expect(stored).toEqual({ enabled: true });
});
