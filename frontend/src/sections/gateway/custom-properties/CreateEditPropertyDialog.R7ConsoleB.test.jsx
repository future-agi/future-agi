// R7-CONSOLE-B, NG-41: the custom property dialog offers what works and says
// where it works.
//   Required and Allowed Values: kept, sent, and labelled as checked by the
//   Validate API (POST /agentcc/custom-properties/validate/), not applied by
//   the gateway to requests.
//   Default: no reader anywhere, so no Default field. A Default stored before
//   is shown read-only as not applied, is never cleared by an unrelated edit,
//   and can be removed on purpose.
//   Log Preview: removed (it showed defaults as if they filled request logs).
// The dialog and list are the gateway console's: sentence-case labels ("Name",
// "One of a list", "Allowed values (Validate API)", "Save") and the list on its grid, drawn
// here as a table by the grid stand-in.
import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CreateEditPropertyDialog from "./CreateEditPropertyDialog";
import CustomPropertySection from "./CustomPropertySection";

// These cases act as an Admin: declaring a property is an Owner or Admin
// write (useGatewayAdmin); ownerOrAdminWrites.test.jsx covers the others.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { properties } = vi.hoisted(() => ({ properties: { current: [] } }));

vi.mock(
  "ag-grid-react",
  async () => await import("../providers/__tests__/grid-mock"),
);
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

const LEGACY = {
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

describe("NG-41: the property dialog", () => {
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
    await user.click(screen.getByRole("option", { name: "One of a list" }));
    expect(
      screen.getByText("Allowed values, checked by the Validate API"),
    ).toBeInTheDocument();
  });

  it("creates with Required and Allowed Values and no default", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog();
    await user.type(screen.getByLabelText(/^Name/), "customer_tier");
    await user.click(screen.getByLabelText("Type"));
    await user.click(screen.getByRole("option", { name: "One of a list" }));
    await user.type(screen.getByPlaceholderText("Add a value"), "gold");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.click(
      screen.getByRole("checkbox", { name: "Required by the Validate API" }),
    );
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
    renderDialog({ property: LEGACY });
    const note = screen.getByText(/Stored default/);
    expect(note.textContent).toContain("us");
    expect(note.textContent).toMatch(/not applied/i);
    expect(screen.queryByLabelText(/Default Value/)).toBeNull();
  });

  it("an edit that does not touch the stored default leaves it alone", async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderDialog({ property: LEGACY });
    await user.clear(screen.getByLabelText("Description"));
    await user.type(screen.getByLabelText("Description"), "Where they are");
    await user.click(screen.getByRole("button", { name: "Save" }));

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
    const { onSubmit } = renderDialog({ property: LEGACY });
    await user.click(
      screen.getByRole("button", { name: "Remove stored default" }),
    );
    expect(screen.queryByText(/Stored default/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit.mock.calls[0][0].default_value).toBeNull();
  });
});

// The list is a gateway page (its frame sets the document title).
const renderList = () =>
  render(
    <HelmetProvider>
      <CustomPropertySection />
    </HelmetProvider>,
  );

describe("NG-41: the property list", () => {
  it("has no Log Preview card", () => {
    properties.current = [LEGACY];
    renderList();
    expect(screen.queryByText("Log Preview")).toBeNull();
    expect(
      screen.queryByText(/how custom properties would appear/i),
    ).toBeNull();
  });

  it("labels a stored default as not applied", () => {
    properties.current = [LEGACY];
    renderList();
    const table = within(screen.getByRole("table"));
    expect(
      table.getByRole("columnheader", { name: "Stored default (not applied)" }),
    ).toBeInTheDocument();
    expect(table.queryByRole("columnheader", { name: "Default" })).toBeNull();
  });

  it("shows no default column when no property has one", () => {
    properties.current = [{ ...LEGACY, default_value: null }];
    renderList();
    const table = within(screen.getByRole("table"));
    expect(table.queryByRole("columnheader", { name: /default/i })).toBeNull();
    expect(
      table.getByRole("columnheader", { name: "Required (Validate API)" }),
    ).toBeInTheDocument();
    expect(
      table.getByRole("columnheader", {
        name: "Allowed values (Validate API)",
      }),
    ).toBeInTheDocument();
  });
});
