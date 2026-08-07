import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import CustomPropertySection from "./CustomPropertySection";

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
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
    render(<CustomPropertySection />);
    const table = within(screen.getByRole("table"));

    expect(table.getByText("priority")).toBeInTheDocument();
    expect(table.getByText("Queue priority")).toBeInTheDocument();
    expect(table.getByText("enum")).toBeInTheDocument();
    expect(table.getAllByText("Required").length).toBeGreaterThanOrEqual(2);
    expect(table.getAllByText("high").length).toBeGreaterThanOrEqual(2);
    expect(table.getByText("low")).toBeInTheDocument();
  });
});
