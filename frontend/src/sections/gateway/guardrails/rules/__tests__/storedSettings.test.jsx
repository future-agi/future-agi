import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderPage } from "../../__tests__/helpers";
import { catalogEntry } from "../../catalog";
import RuleDrawer from "../RuleDrawer";

const tool = catalogEntry("tool-permissions");

function openRule(meta, config) {
  const onSave = vi.fn().mockResolvedValue({});
  renderPage(
    <RuleDrawer
      open
      meta={meta}
      row={{
        id: meta.name,
        label: meta.label,
        check: { config },
        accuracy: null,
      }}
      onClose={() => {}}
      onSave={onSave}
    />,
  );
  return onSave;
}

describe("stored settings drawer", () => {
  it.each([null, ""])("requires deliberate repair of mode %j", async (mode) => {
    const onSave = openRule(tool, {
      ...(mode === undefined ? {} : { mode }),
      tools: "qa_*",
    });
    const drawer = await screen.findByRole("dialog", { name: tool.label });
    const select = within(drawer).getByRole("combobox", { name: "Mode" });
    expect(select).not.toHaveTextContent("denylist");
    expect(
      within(drawer).getByText(/Invalid stored setting/),
    ).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole("option", { name: "denylist" }));
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeEnabled();
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][0].fields.mode).toBe("denylist");
  });

  it("saves an omitted mode without materializing a default", async () => {
    const onSave = openRule(tool, { tools: "qa_*" });
    const drawer = await screen.findByRole("dialog", { name: tool.label });
    expect(
      within(drawer).getByRole("combobox", { name: "Mode" }),
    ).toHaveTextContent("Not stored");
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeEnabled();
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][0].fields.mode).toBeUndefined();
  });

  it.each([" true ", "unknown"])(
    "requires explicit repair of malformed MCP boolean %j without trimming other settings",
    async (validate_inputs) => {
      const meta = catalogEntry("mcp-security");
      const onSave = openRule(meta, {
        validate_inputs,
        blocked_tools: " db_write ",
      });
      const drawer = await screen.findByRole("dialog", { name: meta.label });
      const save = within(drawer).getByRole("button", { name: "Save" });
      expect(
        within(drawer).getByText(/Invalid stored setting/),
      ).toBeInTheDocument();
      expect(save).toBeDisabled();
      fireEvent.click(save);
      expect(onSave).not.toHaveBeenCalled();
      fireEvent.mouseDown(
        within(drawer).getByRole("combobox", { name: "Validate Inputs" }),
      );
      fireEvent.click(
        await screen.findByRole("option", { name: "false", exact: true }),
      );
      expect(save).toBeEnabled();
      fireEvent.click(save);
      expect(onSave.mock.calls[0][0].fields).toMatchObject({
        validate_inputs: "false",
        blocked_tools: " db_write ",
      });
    },
  );

  it.each([true, false])(
    "keeps native boolean %j editable and typed on Save",
    async (validate_inputs) => {
      const meta = catalogEntry("mcp-security");
      const onSave = openRule(meta, {
        validate_inputs,
        max_calls_per_request: 10,
      });
      const drawer = await screen.findByRole("dialog", { name: meta.label });
      expect(
        within(drawer).getByRole("combobox", { name: "Validate Inputs" }),
      ).toHaveTextContent(String(validate_inputs));
      expect(
        within(drawer).getByRole("button", { name: "Save" }),
      ).toBeEnabled();
      fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
      expect(onSave.mock.calls[0][0].fields.validate_inputs).toBe(
        validate_inputs,
      );
    },
  );
});
