import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import CustomPropertySection from "../CustomPropertySection";

// These cases act as an Admin: declaring, editing, deleting and test-sending
// are Owner or Admin writes (useGatewayAdmin); ownerOrAdminWrites.test.jsx
// covers the other roles.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const grids = vi.hoisted(() => ({ list: [] }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grids.list.push(props);
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const state = vi.hoisted(() => ({
  properties: [],
  loading: false,
  error: null,
  refetch: null,
}));

vi.mock("../hooks/useCustomProperties", () => ({
  useCustomProperties: () => ({
    data: state.loading ? undefined : state.properties,
    isLoading: state.loading,
    error: state.error,
    refetch: state.refetch,
  }),
  useCreateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
}));

const FEATURE = {
  id: "p-feature",
  name: "feature",
  description: "Product feature that made the call",
  property_type: "string",
  required: false,
  allowed_values: ["order-summary", "refund-assist", "upsell"],
  default_value: null,
};
const TENANT = {
  id: "p-tenant",
  name: "tenant",
  description: "Customer tenant the call was made for",
  property_type: "string",
  required: false,
  allowed_values: [],
  default_value: null,
};

function renderPage() {
  return render(
    <HelmetProvider>
      <CustomPropertySection />
    </HelmetProvider>,
  );
}

const lastGrid = () => grids.list[grids.list.length - 1];
const column = (id) =>
  lastGrid().columnDefs.find((col) => col.field === id || col.colId === id);

beforeEach(() => {
  grids.list = [];
  state.properties = [FEATURE, TENANT];
  state.loading = false;
  state.error = null;
  state.refetch = vi.fn();
});

describe("Custom properties", () => {
  it("is an FA page with one grid row per declared property", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { name: "Custom properties" }),
    ).toBeInTheDocument();
    expect(lastGrid().rowData.map((row) => row.name)).toEqual([
      "feature",
      "tenant",
    ]);
    expect(
      lastGrid()
        .columnDefs.map((col) => col.headerName)
        .filter(Boolean),
    ).toEqual(
      expect.arrayContaining([
        "Property",
        "Type",
        "Required (Validate API)",
        "Allowed values (Validate API)",
      ]),
    );
    // No property has a stored default, so there is no default column.
    expect(column("stored_default")).toBeUndefined();
  });

  it("shows a stored default only in a column that says it is not applied", () => {
    state.properties = [FEATURE, { ...TENANT, default_value: "acme" }];
    renderPage();
    expect(column("stored_default").headerName).toBe(
      "Stored default (not applied)",
    );
    expect(
      lastGrid().columnDefs.some((col) => col.headerName === "Default"),
    ).toBe(false);
  });

  it("shows Required as a word, not a checkbox", () => {
    renderPage();
    const required = column("required");
    expect(required.cellDataType).toBe("text");
    expect(required.valueFormatter({ value: false })).toBe("Optional");
  });

  it("says where Required and Allowed values are checked when a property uses them", () => {
    renderPage();
    expect(
      screen.getByText(
        /checked by the Validate API \(POST \/agentcc\/custom-properties\/validate\/\).*not applied by the gateway/,
      ),
    ).toBeInTheDocument();
  });

  it("shows no note when no property sets a rule", () => {
    state.properties = [TENANT];
    renderPage();
    expect(screen.queryByText(/checked by the Validate API/)).toBeNull();
  });

  it("marks a property the gateway never records, by its reserved name", () => {
    const reserved = { ...TENANT, id: "p-org", name: "org_unit" };
    state.properties = [TENANT, reserved];
    renderPage();
    const Name = column("name").cellRenderer;
    render(<Name data={lastGrid().rowData[1]} />);
    expect(screen.getByText("Not recorded")).toBeInTheDocument();
  });

  it("shows how to send the properties, not a log preview with defaults", () => {
    renderPage();
    expect(screen.queryByText("Log Preview")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        'x-agentcc-metadata: {"feature":"order-summary","tenant":"<tenant>"}',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs",
    );
  });

  it("has one empty state with the add action", async () => {
    state.properties = [];
    renderPage();
    expect(screen.getByText("No custom properties yet")).toBeInTheDocument();
    await userEvent.click(
      screen.getAllByRole("button", { name: "Add property" })[1],
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("has one error state with Retry", async () => {
    state.error = new Error("Network Error");
    renderPage();
    expect(
      screen.getByText("Couldn't load custom properties"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.refetch).toHaveBeenCalled();
  });

  it("shows a skeleton while loading", () => {
    state.loading = true;
    renderPage();
    expect(screen.getByTestId("custom-properties-loading")).toBeInTheDocument();
  });
});
