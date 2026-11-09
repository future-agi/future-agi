import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ModelCatalogPage from "../ModelCatalogPage";

vi.mock("../../hooks/useCatalogRows", () => ({
  useCatalogRows: () => ({
    status: { data: { available: true, enabled: false } },
    catalog: { data: { providers: [] } },
    rows: [
      {
        id: "own/model",
        name: "Own model",
        vendor: "Own",
        type: "chat",
        access: "byok",
      },
    ],
    offered: true,
    markupPercent: 5,
  }),
}));
vi.mock("../../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../../hooks/useManagedModels", () => ({
  useSetManagedModelsEnabled: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: () => <div data-testid="catalog-grid" />,
}));

describe("F1 catalog footer (S004)", () => {
  it("keeps the catalog and its footer without a new BYOK fee claim", () => {
    render(
      <HelmetProvider>
        <ModelCatalogPage />
      </HelmetProvider>,
    );
    expect(
      screen.getByRole("heading", { name: "Models", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("catalog-grid")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Requests through your own providers use your own provider keys\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/with no markup/)).toBeNull();
  });
});
