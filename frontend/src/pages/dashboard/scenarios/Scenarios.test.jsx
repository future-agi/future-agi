import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen, userEvent, within } from "src/utils/test-utils";

import Scenarios from "./Scenarios";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: { scenarios: { list: "/simulate/scenarios/" } },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE: "create", UPDATE: "update", DELETE: "delete" },
  RolePermission: {
    SIMULATION_AGENT: {
      create: { Owner: true },
      update: { Owner: true },
      delete: { Owner: true },
    },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    scenarioNameClicked: "scenario_name_clicked",
    scenarioAddClicked: "scenario_add_clicked",
  },
  PropertyName: { id: "id", click: "click" },
  trackEvent: vi.fn(),
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/page-header", () => ({ default: () => null }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/components/scenarios/ScenariosActionMenu", () => ({
  default: () => null,
}));
vi.mock("src/components/scenarios/DeleteScenarioDialog", () => ({
  default: () => null,
}));
vi.mock("src/components/scenarios/EditScenarioDialog", () => ({
  default: () => null,
}));
vi.mock("./SimulationScenarioEmptyScreen", () => ({ default: () => null }));

// A plain table that calls each column's cell renderer the same way
// src/components/data-table/DataTable.jsx does, so the test reads the
// columns the page defines without MUI DataGrid layout in jsdom.
vi.mock("src/components/data-table", async () => {
  const { default: PropTypes } = await import("prop-types");

  function DataTable({ columns, data }) {
    return (
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.id}>{col.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.id}>
              {columns.map((col) => (
                <td key={col.id}>
                  {col.cell
                    ? col.cell({
                        getValue: () => row[col.accessorKey || col.id],
                        row: { original: row, getIsSelected: () => false },
                        column: col,
                      })
                    : row[col.accessorKey || col.id]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  DataTable.propTypes = {
    columns: PropTypes.array.isRequired,
    data: PropTypes.array.isRequired,
  };

  return { DataTable, DataTablePagination: () => null };
});

const LONG_DESCRIPTION =
  "Caller disputes a duplicate charge on their March invoice, asks for a refund to the original card, and asks for a supervisor when offered store credit instead.";

const scenario = (id, name, extra = {}) => ({
  id,
  name,
  agent_type: "voice",
  dataset_rows: 20,
  status: "Completed",
  scenario_type: "dataset",
  created_at: "2026-07-07T15:30:15Z",
  ...extra,
});

const SCENARIOS = [
  scenario("sc-1", "Refund dispute", { description: LONG_DESCRIPTION }),
  scenario("sc-2", "Null description", { description: null }),
  scenario("sc-3", "Missing description"),
  scenario("sc-4", "Empty description", { description: "" }),
];

const renderScenarios = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Scenarios />
    </QueryClientProvider>,
  );
};

const headerNames = () =>
  screen.getAllByRole("columnheader").map((th) => th.textContent);

const descriptionCell = (scenarioName) => {
  const index = headerNames().indexOf("Description");
  expect(index).toBeGreaterThan(-1);
  const row = screen.getByText(scenarioName).closest("tr");
  return within(row).getAllByRole("cell")[index];
};

describe("Scenarios list description column", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({
      data: { results: SCENARIOS, count: SCENARIOS.length },
    });
  });

  it("shows a Description column between Name and Agent Type and keeps the other columns", async () => {
    renderScenarios();
    await screen.findByText("Refund dispute");

    expect(headerNames()).toEqual([
      "Name",
      "Description",
      "Agent Type",
      "No of Datapoints",
      "Status",
      "Scenario Type",
      "Created At",
      "",
    ]);
  });

  it("shows the description entered on creation in the Description cell", async () => {
    renderScenarios();
    await screen.findByText("Refund dispute");

    expect(descriptionCell("Refund dispute")).toHaveTextContent(
      LONG_DESCRIPTION,
    );
  });

  it("shows an em dash when the description is null, missing or empty", async () => {
    renderScenarios();
    await screen.findByText("Refund dispute");

    for (const name of [
      "Null description",
      "Missing description",
      "Empty description",
    ]) {
      expect(descriptionCell(name).textContent).toBe("—");
    }
  });

  it("keeps a long description on one line and shows all of it on hover", async () => {
    const user = userEvent.setup();
    renderScenarios();
    await screen.findByText("Refund dispute");

    const text = within(descriptionCell("Refund dispute")).getByText(
      LONG_DESCRIPTION,
    );
    expect(text).toHaveClass("MuiTypography-noWrap");

    await user.hover(text);
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent(LONG_DESCRIPTION);
  });

  it("shows no tooltip for an empty description", async () => {
    const user = userEvent.setup();
    renderScenarios();
    await screen.findByText("Refund dispute");

    const dash = within(descriptionCell("Empty description")).getByText("—");
    await user.hover(dash);
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
