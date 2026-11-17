/* eslint-disable react/prop-types */
import React from "react";
import { vi } from "vitest";
import { useLocation } from "react-router-dom";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderModelsAt } from "./harness";
import ModelCatalogPage from "../catalog/ModelCatalogPage";
import ModelDetailPage from "../detail/ModelDetailPage";
import { ORACLE_NOW } from "./generate-v2-stock-oracle.mjs";

// Replace data transport, not useCatalogRows, rows, pages, price formatters,
// columns, FactGrid, or any cell renderer. No provider/browser calls allowed.
// The managed status, the managed catalog, the org's provider health and
// each route's request logs are read through the pages' real React Query
// hooks: the transport answers those reads from the frozen data, as the API
// does (no requests in the frozen window), and refuses every other request.
// The pages' retained reads confirm each value by reading it again, so a
// page is read once those reads have settled.
const data = vi.hoisted(() => ({ catalog: null, status: null }));
vi.mock("../hooks/useVendorLogos", () => ({ useVendorLogos: () => new Map() }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: {
      id: "default",
      baseUrl: "http://localhost:28490/v1",
    },
    // A confirmed self-hosted deployment: ModelApi withholds request snippets
    // until the deployment mode is confirmed (snippetBaseUrl, since a390b39c).
    hosted: false,
    deploymentModeConfirmed: true,
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
  const gw = actual.endpoints.gateway;
  const answers = () => ({
    [gw.managedModels]: data.status,
    [gw.managedModelsCatalog]: data.catalog,
    [gw.providers("default")]: { providers: [] },
  });
  const get = async (url) => {
    // The request-logs list answers DRF's page shape: no traffic.
    if (url === gw.requestLogs) return { data: { count: 0, results: [] } };
    const result = answers()[url];
    if (result === undefined) refuse();
    return { data: { status: true, result } };
  };
  return {
    ...actual,
    default: {
      ...actual.default,
      get,
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
let client;
// Until no read of the page is in flight.
const settled = () =>
  waitFor(
    () => {
      if (client?.isFetching()) throw new Error("The page is still reading");
    },
    { timeout: 15000 },
  );

// Renders the catalogue and waits until its reads have answered and it shows
// rows; the tests then read the page as it stands, so a missing or extra row
// still fails their own assertions.
export async function showCatalog(catalog, models = catalog.models) {
  cleanup();
  clock?.mockRestore();
  clock = vi.spyOn(Date, "now").mockReturnValue(ORACLE_NOW);
  data.catalog = { ...catalog, models };
  data.status = {
    available: true,
    enabled: true,
    balance_usd: 35,
    markup_percent: catalog.markup_percent,
  };
  const rendered = renderModelsAt("/dashboard/gateway/models", <Pages />);
  ({ client } = rendered);
  await waitFor(
    () => {
      const shown =
        rendered.container.querySelectorAll("[data-model-id]").length;
      if (models.length && !shown)
        throw new Error("The catalogue shows no rows");
    },
    { timeout: 15000 },
  );
  await settled();
  return rendered;
}

export function finishOracle() {
  cleanup();
  clock?.mockRestore();
  clock = undefined;
  client = undefined;
}

export async function openDetails(row) {
  fireEvent.click(row.querySelector("[data-col='name'] a"));
  const pricing = await screen.findByRole("region", { name: "Pricing" });
  await settled();
  return pricing;
}

export function openApi() {
  fireEvent.click(screen.getByRole("tab", { name: "API", exact: true }));
}
