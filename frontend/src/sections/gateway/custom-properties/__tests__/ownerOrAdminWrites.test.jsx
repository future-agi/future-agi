import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import CustomPropertySection from "../CustomPropertySection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../../testing/adminOnly";

// Adding, editing and deleting a custom property is a gateway write: the
// console offers it to an organization Owner or Admin, as on Providers, API
// keys and Budgets (useGatewayAdmin). Anyone else sees each control
// disabled, with the role it needs.

const grids = vi.hoisted(() => ({ list: [] }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grids.list.push(props);
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const state = vi.hoisted(() => ({ orgLevel: 8, properties: [] }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));

vi.mock("../hooks/useCustomProperties", () => ({
  useCustomProperties: () => ({
    data: state.properties,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
}));

const TENANT = {
  id: "p-tenant",
  name: "tenant",
  description: "Customer tenant",
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

// The row's Edit and Delete, rendered from the grid's actions column with
// the context the page gives it.
function renderRowActions() {
  const grid = lastGrid();
  const actions = grid.columnDefs.find((col) => col.colId === "actions");
  const Actions = actions.cellRenderer;
  render(<Actions data={TENANT} context={grid.context} />);
}

beforeEach(() => {
  grids.list = [];
  state.properties = [TENANT];
});

describe("Custom properties: only an Owner or Admin adds, edits or deletes", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Add property, Edit and Delete disabled, with the role they need",
    (_who, level) => {
      state.orgLevel = level;
      renderPage();
      expectAdminOnly(screen.getByRole("button", { name: "Add property" }));
      renderRowActions();
      expectAdminOnly(screen.getByRole("button", { name: "Edit tenant" }));
      expectAdminOnly(screen.getByRole("button", { name: "Delete tenant" }));
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s is not offered Add property from the empty list",
    (_who, level) => {
      state.orgLevel = level;
      state.properties = [];
      renderPage();
      // Only the page's own Add property, disabled: the empty list offers
      // none.
      const adds = screen.getAllByRole("button", { name: "Add property" });
      expect(adds).toHaveLength(1);
      expectAdminOnly(adds[0]);
    },
  );

  it.each(ADMIN_LEVELS)("%s can add, edit and delete", (_who, level) => {
    state.orgLevel = level;
    renderPage();
    expectEditable(screen.getByRole("button", { name: "Add property" }));
    renderRowActions();
    expectEditable(screen.getByRole("button", { name: "Edit tenant" }));
    expectEditable(screen.getByRole("button", { name: "Delete tenant" }));
  });
});
