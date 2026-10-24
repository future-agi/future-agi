// The custom property dialog offers what works and says where it works.
//   Required and Allowed Values: kept, sent, and labelled as checked by the
//   Validate API (POST /agentcc/custom-properties/validate/), not applied by
//   the gateway to requests.
//   Default: no reader anywhere, so no Default field. A stored Default is
//   shown read-only as not applied, is never cleared by an unrelated edit,
//   and can be removed on purpose.
//   Log Preview: not offered.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CreateEditPropertyDialog from "./CreateEditPropertyDialog";
import CustomPropertySection from "./CustomPropertySection";

const { properties } = vi.hoisted(() => ({ properties: { current: [] } }));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("./hooks/useCustomProperties", () => ({
  useCustomProperties: () => ({
    data: properties.current,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
}));

const VALIDATE_SENTENCE =
  /checked by the Validate API \(POST \/agentcc\/custom-properties\/validate\/\).*not applied by the gateway/i;

const STORED_DEFAULT_PROPERTY = {
  id: "prop-1",
  name: "region",
  description: "Customer region",
  property_type: "enum",
  required: true,
  allowed_values: ["us", "eu"],
  default_value: "us",
};

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
  return { onSubmit };
}

beforeEach(() => {
  properties.current = [];
});

describe("the property dialog", () => {
  it("offers no Default field", () => {
    renderDialog();
    expect(screen.queryByLabelText(/Default Value/)).toBeNull();
    expect(screen.queryByText(/Default Value/)).toBeNull();
  });

  it("says Required and Allowed Values are checked by the Validate API, not by the gateway", async () => {
    const user = userEvent.setup();
    renderDialog();
    expect(screen.getByText(VALIDATE_SENTENCE)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Type"));
    await user.click(screen.getByRole("option", { name: "Enum" }));
    expect(screen.getByText("Allowed Values")).toBeInTheDocument();
  });

  it("creates with Required and Allowed Values and no default", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog();
    await user.type(screen.getByLabelText(/Property Name/), "customer_tier");
    await user.click(screen.getByLabelText("Type"));
    await user.click(screen.getByRole("option", { name: "Enum" }));
    await user.type(screen.getByPlaceholderText("Add enum value"), "gold");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.click(screen.getByRole("checkbox", { name: "Required" }));
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toMatchObject({
      name: "customer_tier",
      property_type: "enum",
      required: true,
      allowed_values: ["gold"],
    });
    expect(payload).not.toHaveProperty("default_value");
  });

  it("shows a stored default read-only as not applied", () => {
    renderDialog({ property: STORED_DEFAULT_PROPERTY });
    const note = screen.getByText(/Stored default/);
    expect(note.textContent).toContain("us");
    expect(note.textContent).toMatch(/not applied/i);
    expect(screen.queryByLabelText(/Default Value/)).toBeNull();
  });

  it("an edit that does not touch the stored default leaves it alone", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ property: STORED_DEFAULT_PROPERTY });
    await user.clear(screen.getByLabelText("Description"));
    await user.type(screen.getByLabelText("Description"), "Where they are");
    await user.click(screen.getByRole("button", { name: "Update" }));

    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toMatchObject({
      id: "prop-1",
      description: "Where they are",
      required: true,
      allowed_values: ["us", "eu"],
    });
    expect(payload).not.toHaveProperty("default_value");
  });

  it("Remove stored default sends null on purpose", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ property: STORED_DEFAULT_PROPERTY });
    await user.click(
      screen.getByRole("button", { name: "Remove stored default" }),
    );
    expect(screen.queryByText(/Stored default/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Update" }));
    expect(onSubmit.mock.calls[0][0].default_value).toBeNull();
  });
});

describe("the property list", () => {
  it("has no Log Preview card", () => {
    properties.current = [STORED_DEFAULT_PROPERTY];
    render(<CustomPropertySection />);
    expect(screen.queryByText("Log Preview")).toBeNull();
    expect(
      screen.queryByText(/how custom properties would appear/i),
    ).toBeNull();
  });

  it("labels a stored default as not applied", () => {
    properties.current = [STORED_DEFAULT_PROPERTY];
    render(<CustomPropertySection />);
    const table = within(screen.getByRole("table"));
    expect(
      table.getByRole("columnheader", { name: "Stored default (not applied)" }),
    ).toBeInTheDocument();
    expect(table.queryByRole("columnheader", { name: "Default" })).toBeNull();
  });

  it("shows no default column when no property has one", () => {
    properties.current = [{ ...STORED_DEFAULT_PROPERTY, default_value: null }];
    render(<CustomPropertySection />);
    const table = within(screen.getByRole("table"));
    expect(table.queryByRole("columnheader", { name: /default/i })).toBeNull();
    expect(
      table.getByRole("columnheader", { name: "Required" }),
    ).toBeInTheDocument();
    expect(
      table.getByRole("columnheader", { name: "Allowed Values" }),
    ).toBeInTheDocument();
  });
});
