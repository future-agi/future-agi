/* eslint-disable react/prop-types */
import React from "react";
import { vi } from "vitest";
import { useLocation } from "react-router-dom";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderModelsAt } from "./harness";
import ModelCatalogPage from "../catalog/ModelCatalogPage";
import ModelDetailPage from "../detail/ModelDetailPage";
import { ORACLE_NOW } from "./generate-v2-stock-oracle.mjs";

// Replace data transport, not useCatalogRows, rows, pages, price formatters,
// columns, FactGrid, or any cell renderer. No provider/browser calls allowed.
const data = vi.hoisted(() => ({ catalog: null }));
vi.mock("../hooks/useManagedModels", async (original) => ({
  ...(await original()),
  useManagedCatalog: () => ({ data: data.catalog, isLoading: false }),
  useManagedModelsStatus: () => ({
    data: {
      available: true,
      enabled: true,
      balance_usd: 35,
      markup_percent: data.catalog.markup_percent,
    },
    isLoading: false,
  }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", async (original) => ({
  ...(await original()),
  useProviderHealth: () => ({ data: { providers: [] }, isLoading: false }),
}));
vi.mock("../hooks/useVendorLogos", () => ({ useVendorLogos: () => new Map() }));
vi.mock("../hooks/useRouteHealth", () => ({
  useRouteHealth: () => ({ items: [], isLoading: false }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: {
      id: "default",
      baseUrl: "http://localhost:28490/v1",
    },
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  const refuse = () => {
    throw new Error("Oracle forbids HTTP transport");
  };
  return {
    ...actual,
    default: {
      ...actual.default,
      get: refuse,
      post: refuse,
      put: refuse,
      patch: refuse,
      delete: refuse,
      request: refuse,
    },
  };
});

// Only grid virtualization/DOM layout is substituted. Invoke the REAL column
// value getters, formatters and renderers, including spanning and hidden price
// cells, so missing/extra dimensions and duplicate output prices fail.
vi.mock("ag-grid-react", () => ({
  AgGridReact: function OracleGrid({
    rowData = [],
    columnDefs = [],
    onRowClicked,
    getRowId,
  }) {
    return (
      <div role="grid">
        <div role="row">
          {columnDefs.map((col) => (
            <div
              role="columnheader"
              key={col.colId || col.field}
              data-col={col.colId || col.field}
              title={col.headerTooltip}
            >
              {col.headerName}
            </div>
          ))}
        </div>
        {rowData.map((row, index) => {
          let skip = 0;
          const id =
            getRowId?.({ data: row }) || row.id || row.key || String(index);
          return (
            <div
              role="row"
              data-model-id={row.id}
              data-row-id={id}
              key={id}
              tabIndex={0}
              onClick={() => onRowClicked?.({ data: row })}
              onKeyDown={() => {}}
            >
              {columnDefs.map((col) => {
                if (skip) {
                  skip -= 1;
                  return null;
                }
                const span = col.colSpan?.({ data: row }) || 1;
                skip = span - 1;
                const value = col.valueGetter
                  ? col.valueGetter({ data: row })
                  : row[col.field];
                const valueFormatted = col.valueFormatter
                  ? col.valueFormatter({ data: row, value })
                  : value;
                const Renderer = col.cellRenderer;
                return (
                  <div
                    role="gridcell"
                    key={col.colId || col.field}
                    data-col={col.colId || col.field}
                    data-span={span}
                    data-auto-height={String(Boolean(col.autoHeight))}
                  >
                    {Renderer ? (
                      <Renderer
                        data={row}
                        value={value}
                        valueFormatted={valueFormatted}
                      />
                    ) : (
                      String(valueFormatted ?? "")
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    );
  },
}));

function Pages() {
  const location = useLocation();
  return location.pathname === "/dashboard/gateway/models" ? (
    <ModelCatalogPage />
  ) : (
    <ModelDetailPage />
  );
}

let clock;
export function showCatalog(catalog, models = catalog.models) {
  cleanup();
  clock?.mockRestore();
  clock = vi.spyOn(Date, "now").mockReturnValue(ORACLE_NOW);
  data.catalog = { ...catalog, models };
  return renderModelsAt("/dashboard/gateway/models", <Pages />);
}

export function finishOracle() {
  cleanup();
  clock?.mockRestore();
  clock = undefined;
}

export function openDetails(row) {
  fireEvent.click(row.querySelector("[data-col='name'] a"));
  return screen.getByRole("region", { name: "Pricing" });
}

export function openApi() {
  fireEvent.click(screen.getByRole("tab", { name: "API", exact: true }));
}
