import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { ACCESS, buildCatalogRows } from "../catalogRows";
import { catalogColumns } from "../catalog/columns";
import ModelOverview from "../detail/ModelOverview";

// Price reconciliation, Models page: a model's price is the one number the
// org pays per 1M tokens on its route. On Future AGI credits that is the
// billed price (what a request is charged); a model only an own key reaches
// shows the provider's price. Neither shows the other price beside it, a
// margin or a percentage.

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  const { default: PropTypes } = await import("prop-types");
  function MockGrid({ columnDefs, ...props }) {
    const shown = columnDefs.filter((col) => !col.hide);
    return (
      <>
        <div>
          {shown.map((col) => (
            <div role="columnheader" key={col.colId || col.field}>
              {col.headerName}
            </div>
          ))}
        </div>
        <MockAgGrid columnDefs={shown} {...props} />
      </>
    );
  }
  MockGrid.propTypes = {
    columnDefs: PropTypes.arrayOf(PropTypes.object).isRequired,
  };
  return { AgGridReact: MockGrid };
});
vi.mock("../detail/ModelHealth", () => ({ default: () => null }));

const MARKUP = 5;

// $4.00 / $16.00 per 1M from the provider; the catalog's billed price on
// credits is $4.20 / $16.80.
const CREDITS_MODEL = {
  id: "openai/gpt-recon",
  name: "GPT Recon",
  vendor: "openai",
  state: "active",
  type: "chat",
  input_modalities: ["text"],
  output_modalities: ["text"],
  context_length: 128000,
  prices: { prompt: "0.000004", completion: "0.000016" },
  billed_input_per_mtok: 4.2,
  billed_output_per_mtok: 16.8,
  created: 1790000000,
};
// $1.30 / $5.20 per 1M; not sold on credits, reached by the org's own key.
const OWN_KEY_MODEL = {
  id: "acme/own-recon",
  name: "Own Recon",
  vendor: "acme",
  state: "active",
  type: "chat",
  input_modalities: ["text"],
  output_modalities: ["text"],
  context_length: 32000,
  prices: { prompt: "0.0000013", completion: "0.0000052" },
  managed_allowed: false,
  created: 1790000000,
};

const rows = buildCatalogRows([CREDITS_MODEL, OWN_KEY_MODEL], {
  providers: [{ id: "acme", name: "Acme", models: ["own-recon"] }],
  managedStatus: { available: true, enabled: true, markup_percent: MARKUP },
});
const rowOf = (id) => rows.find((row) => row.id === id);

const theme = createTheme({ palette: palette("light"), typography });
function renderOverview(row) {
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <ModelOverview row={row} markupPercent={MARKUP} />
      </MemoryRouter>
    </ThemeProvider>,
  );
  const pricing = screen.getByRole("region", { name: "Pricing" });
  const headers = within(pricing)
    .getAllByRole("columnheader")
    .map((th) => th.textContent);
  const line = (label) =>
    within(pricing)
      .getAllByRole("row")
      .find((r) => r.firstChild?.textContent === label);
  const cells = (label) =>
    [...line(label).querySelectorAll("[role='gridcell']")].map(
      (cell) => cell.textContent,
    );
  return { pricing, headers, cells };
}

const NO_SPLIT = /markup|future agi price|provider price|provider \+|%/i;

describe("Models page price reconciliation", () => {
  it("builds one sold row and one own-key row", () => {
    expect(rowOf(CREDITS_MODEL.id).access).toBe(ACCESS.MANAGED);
    expect(rowOf(OWN_KEY_MODEL.id).access).toBe(ACCESS.OWN_KEY);
  });

  it("prices a credits model in the catalog at its billed price, with no margin in the headers", () => {
    const columns = catalogColumns({ markupPercent: MARKUP });
    const input = columns.find((col) => col.colId === "input");
    const output = columns.find((col) => col.colId === "output");
    const row = rowOf(CREDITS_MODEL.id);
    expect(input.valueFormatter({ data: row, value: row.input })).toBe("$4.20");
    expect(output.valueFormatter({ data: row, value: row.output })).toBe(
      "$16.80",
    );
    for (const col of [input, output]) {
      expect(`${col.headerName} ${col.headerTooltip || ""}`).not.toMatch(
        NO_SPLIT,
      );
    }
  });

  it("prices a credits model on its page at the billed price only", () => {
    const { pricing, headers, cells } = renderOverview(rowOf(CREDITS_MODEL.id));
    expect(headers).toEqual(["Per 1M tokens", "Price"]);
    expect(cells("Input")).toEqual(["Input", "$4.20"]);
    expect(cells("Output")).toEqual(["Output", "$16.80"]);
    expect(within(pricing).queryByText("$4.00")).toBeNull();
    expect(within(pricing).queryByText("$16.00")).toBeNull();
    expect(pricing.textContent).not.toMatch(NO_SPLIT);
    const routes = screen.getByRole("region", { name: "Routes" });
    expect(routes.textContent).not.toMatch(NO_SPLIT);
  });

  it("prices an own-key model on its page at the provider's price", () => {
    const { pricing, headers, cells } = renderOverview(rowOf(OWN_KEY_MODEL.id));
    expect(headers).toEqual(["Per 1M tokens", "Price"]);
    expect(cells("Input")).toEqual(["Input", "$1.30"]);
    expect(cells("Output")).toEqual(["Output", "$5.20"]);
    expect(pricing.textContent).not.toMatch(NO_SPLIT);
  });
});
