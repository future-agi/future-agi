import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import CreateEditPropertyDialog from "../CreateEditPropertyDialog";

function renderDialog(props = {}) {
  const onSubmit = vi.fn();
  render(
    <CreateEditPropertyDialog
      open
      onClose={vi.fn()}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return onSubmit;
}

// Required and Allowed values are checked by the Validate API, so the
// dialog sets them; a Default has no reader anywhere, so there is no Default
// field, and one stored before shows read-only as not applied.
describe("Add property dialog", () => {
  it("offers a name, a description, a type and Required, and no Default", async () => {
    const onSubmit = renderDialog();
    expect(
      screen.getByRole("checkbox", { name: "Required by the Validate API" }),
    ).not.toBeChecked();
    expect(
      screen.queryByText("Allowed values, checked by the Validate API"),
    ).toBeNull();
    expect(screen.queryByLabelText(/Default/)).toBeNull();

    await userEvent.click(screen.getByLabelText(/Type/));
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Text", "Number", "Boolean", "One of a list"]);
    await userEvent.click(screen.getByRole("option", { name: "Number" }));

    await userEvent.type(screen.getByLabelText(/^Name/), "seats");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(onSubmit).toHaveBeenCalledWith({
      name: "seats",
      description: "",
      property_type: "number",
      required: false,
      allowed_values: [],
    });
  });

  it("needs at least one value for a list", async () => {
    renderDialog();
    await userEvent.type(screen.getByLabelText(/^Name/), "plan");
    await userEvent.click(screen.getByLabelText(/Type/));
    await userEvent.click(
      screen.getByRole("option", { name: "One of a list" }),
    );
    const create = screen.getByRole("button", { name: "Create" });
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText("Add a value"), "free");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(create).toBeEnabled();
  });

  it("edits a stored property's rules and keeps its stored default", async () => {
    const onSubmit = renderDialog({
      property: {
        id: "p-plan",
        name: "plan",
        description: "Billing plan",
        property_type: "enum",
        required: true,
        allowed_values: ["free", "pro"],
        default_value: "free",
      },
    });
    expect(
      screen.getByRole("checkbox", { name: "Required by the Validate API" }),
    ).toBeChecked();
    expect(screen.getByText("pro")).toBeInTheDocument();
    expect(screen.getByText(/Stored default: free/)).toHaveTextContent(
      /Not applied/,
    );
    // Its stored type still reads as such.
    expect(screen.getByLabelText(/Type/)).toHaveTextContent("One of a list");

    await userEvent.clear(screen.getByLabelText(/Description/));
    await userEvent.type(screen.getByLabelText(/Description/), "Plan tier");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).toHaveBeenCalledWith({
      id: "p-plan",
      name: "plan",
      description: "Plan tier",
      property_type: "enum",
      required: true,
      allowed_values: ["free", "pro"],
    });
  });

  it("shows no stored default for a property with none", () => {
    renderDialog({
      property: {
        id: "p-feature",
        name: "feature",
        property_type: "string",
        required: false,
        allowed_values: [],
        default_value: null,
      },
    });
    expect(screen.queryByText(/Stored default/)).toBeNull();
  });

  it("refuses a name the gateway never records", async () => {
    const onSubmit = renderDialog();
    await userEvent.type(screen.getByLabelText(/^Name/), "org_unit");
    expect(screen.getByText(/the gateway drops it/)).toBeInTheDocument();
    const create = screen.getByRole("button", { name: "Create" });
    expect(create).toBeDisabled();

    await userEvent.clear(screen.getByLabelText(/^Name/));
    await userEvent.type(screen.getByLabelText(/^Name/), "business_unit");
    expect(create).toBeEnabled();
    await userEvent.click(create);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ name: "business_unit" }),
    );
  });
});

// The server checks a stored default against the type a save sends
// (agentcc/serializers/custom_property.py validate). So while the stored
// default is kept, a type it does not fit is not offered (the save would be
// refused), and the option says why; removing the stored default lifts it.
describe("Edit property: the type and a stored default", () => {
  const typeOptions = async () => {
    await userEvent.click(screen.getByLabelText(/Type/));
    return screen.getAllByRole("option");
  };

  it("does not offer a type the stored default does not fit, and says why", async () => {
    renderDialog({
      property: {
        id: "p1",
        name: "tenant",
        property_type: "string",
        default_value: "acme",
        allowed_values: [],
        required: false,
      },
    });
    const [text, number, boolean] = await typeOptions();
    expect(text).not.toHaveAttribute("aria-disabled", "true");
    expect(number).toHaveAttribute("aria-disabled", "true");
    expect(number).toHaveTextContent(
      'The stored default "acme" is not a number',
    );
    expect(boolean).toHaveAttribute("aria-disabled", "true");
    expect(boolean).toHaveTextContent("is not true or false");
  });

  it("keeps a list property's other types that the default fits", async () => {
    renderDialog({
      property: {
        id: "p2",
        name: "tier",
        property_type: "enum",
        default_value: "gold",
        allowed_values: ["gold", "silver"],
        required: true,
      },
    });
    const options = await typeOptions();
    const byLabel = (label) =>
      options.find((o) => o.textContent.startsWith(label));
    expect(byLabel("Text")).not.toHaveAttribute("aria-disabled", "true");
    expect(byLabel("Number")).toHaveAttribute("aria-disabled", "true");
    expect(byLabel("One of a list")).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("offers every type once the stored default is removed", async () => {
    renderDialog({
      property: {
        id: "p4",
        name: "tenant",
        property_type: "string",
        default_value: "acme",
        allowed_values: [],
        required: false,
      },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Remove stored default" }),
    );
    for (const option of await typeOptions()) {
      expect(option).not.toHaveAttribute("aria-disabled", "true");
    }
  });

  it("offers every type when no default is stored", async () => {
    renderDialog({
      property: {
        id: "p3",
        name: "feature",
        property_type: "string",
        default_value: null,
        allowed_values: [],
        required: true,
      },
    });
    for (const option of await typeOptions()) {
      expect(option).not.toHaveAttribute("aria-disabled", "true");
    }
  });
});
