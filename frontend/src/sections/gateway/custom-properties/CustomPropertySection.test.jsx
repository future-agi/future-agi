import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CustomPropertySection from "./CustomPropertySection";

// Schema writes require Owner/Admin; metadata validation is also open to readers.
const state = vi.hoisted(() => ({ orgLevel: 8, validate: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

vi.mock("./hooks/useCustomProperties", () => ({
  useCustomProperties: () => ({
    data: [
      {
        id: "property-1",
        name: "priority",
        description: "Queue priority",
        property_type: "enum",
        required: true,
        allowed_values: ["high", "low"],
        default_value: "high",
      },
    ],
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteCustomProperty: () => ({ mutate: vi.fn(), isPending: false }),
  useValidateCustomProperties: () => ({
    mutate: state.validate,
    isPending: false,
  }),
}));

beforeEach(() => {
  state.orgLevel = 8;
  state.validate.mockReset();
  state.validate.mockImplementation((_payload, { onSuccess }) =>
    onSuccess({ valid: true, errors: [] }),
  );
});

describe("CustomPropertySection", () => {
  it.each([
    ["Viewer", 1, false],
    ["Member", 3, false],
    ["Admin", 8, true],
    ["Owner", 15, true],
  ])("lets %s validate while preserving schema write gates", async (_role, level, canEdit) => {
    state.orgLevel = level;
    const user = userEvent.setup();
    render(
      <HelmetProvider>
        <CustomPropertySection />
      </HelmetProvider>,
    );
    const Actions = grid.props.columnDefs.find(
      (column) => column.colId === "actions",
    ).cellRenderer;
    render(<Actions data={grid.props.rowData[0]} context={grid.props.context} />);

    for (const name of ["Add property", "Edit priority", "Delete priority"]) {
      const button = screen.getByRole("button", { name });
      if (canEdit) expect(button).toBeEnabled();
      else expect(button).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: "Test metadata" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Test metadata" }));
    const dialog = screen.getByRole("dialog", { name: "Test metadata" });
    expect(state.validate).not.toHaveBeenCalled();
    expect(within(dialog).getByRole("textbox", { name: "Metadata JSON" })).toHaveValue(
      JSON.stringify({ priority: "high" }, null, 2),
    );
    await user.click(within(dialog).getByRole("button", { name: "Validate" }));
    expect(state.validate).toHaveBeenCalledTimes(1);
    expect(state.validate.mock.calls[0][0]).toEqual({ properties: { priority: "high" } });
    expect(within(dialog).getByText(/Passed validation/)).toBeInTheDocument();
  });

  it("uses all saved schemas despite a list filter and resets the draft when reopened", async () => {
    const user = userEvent.setup();
    render(
      <HelmetProvider>
        <CustomPropertySection />
      </HelmetProvider>,
    );
    await user.type(screen.getByPlaceholderText("Search properties"), "not-present");
    expect(grid.props.rowData).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Test metadata" }));
    const field = screen.getByRole("textbox", { name: "Metadata JSON" });
    expect(JSON.parse(field.value)).toEqual({ priority: "high" });
    fireEvent.change(field, { target: { value: '{"priority":"low"}' } });
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Test metadata" }));
    expect(JSON.parse(screen.getByRole("textbox", { name: "Metadata JSON" }).value)).toEqual({
      priority: "high",
    });
    expect(state.validate).not.toHaveBeenCalled();
  });
  it("renders enum values and default from canonical snake_case API fields", () => {
    render(
      <HelmetProvider>
        <CustomPropertySection />
      </HelmetProvider>,
    );
    const col = (field) => grid.props.columnDefs.find((c) => c.field === field);
    const row = grid.props.rowData[0];

    expect(row.name).toBe("priority");
    expect(col("property_type").valueFormatter({ value: "enum" })).toBe(
      "One of a list",
    );
    expect(col("required").valueFormatter({ value: row.required })).toBe(
      "Required",
    );
    expect(col("default_value").valueFormatter({ value: "high" })).toBe("high");

    const Name = col("name").cellRenderer;
    const Allowed = col("allowed_values").cellRenderer;
    render(
      <>
        <Name data={row} />
        <Allowed value={row.allowed_values} />
      </>,
    );
    expect(screen.getByText("Queue priority")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("low")).toBeInTheDocument();
  });

  it("leaves room for a sort arrow beside the Required and stored default headers", () => {
    render(
      <HelmetProvider>
        <CustomPropertySection />
      </HelmetProvider>,
    );
    const required = grid.props.columnDefs.find((c) => c.field === "required");
    expect(required.minWidth).toBeGreaterThanOrEqual(144);
    expect(
      grid.props.columnDefs.find((c) => c.field === "default_value").minWidth,
    ).toBeGreaterThanOrEqual(200);
  });

  it("keeps the +N count whole: long values shrink with an ellipsis instead", () => {
    render(
      <HelmetProvider>
        <CustomPropertySection />
      </HelmetProvider>,
    );
    const Allowed = grid.props.columnDefs.find(
      (c) => c.field === "allowed_values",
    ).cellRenderer;
    render(
      <Allowed
        value={["order-summary", "refund-assist", "upsell", "a", "b"]}
      />,
    );
    expect(screen.getByText("+2")).toHaveStyle({ flexShrink: "0" });
    // jsdom does not compute text-overflow; min-width 0 and overflow hidden
    // are what let a chip give way.
    expect(screen.getByText("order-summary")).toHaveStyle(
      "min-width: 0; overflow: hidden",
    );
  });
});
