import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CustomPropertySection from "./CustomPropertySection";

// These cases act as an Admin: declaring, editing, deleting and test-sending
// are Owner or Admin writes (useGatewayAdmin); ownerOrAdminWrites.test.jsx
// covers the other roles.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
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
}));

describe("CustomPropertySection", () => {
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
