import { describe, it, expect, vi, beforeEach } from "vitest";

// Hoisted so the mock factory can reference them.
const dtMock = vi.hoisted(() => vi.fn(() => null));
const qState = vi.hoisted(() => ({ results: [], count: 0 }));
const qLoading = vi.hoisted(() => ({ current: false }));

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(() => ({ data: qState, isLoading: qLoading.current })),
  useQueryClient: vi.fn(() => ({ invalidateQueries: vi.fn() })),
}));

vi.mock("react-router-dom", async (imp) => {
  const a = await imp();
  return { ...a, useNavigate: vi.fn(() => vi.fn()) };
});

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: vi.fn(() => ({ role: "owner" })) }));
vi.mock("src/utils/rolePermissionMapping", () => ({
  RolePermission: { SIMULATION_AGENT: { CREATE: { owner: true }, UPDATE: { owner: true }, DELETE: { owner: true } } },
  PERMISSIONS: { CREATE: "CREATE", UPDATE: "UPDATE", DELETE: "DELETE" },
}));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, PropertyName: {}, trackEvent: vi.fn() }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({ default: () => null }));

vi.mock("src/components/data-table", () => ({
  DataTable: dtMock,
  DataTablePagination: () => null,
}));

vi.mock("src/components/scenarios/DeleteScenarioDialog", () => ({ default: () => null }));
vi.mock("src/components/scenarios/EditScenarioDialog", () => ({ default: () => null }));
vi.mock("src/components/scenarios/ScenariosActionMenu", () => ({ default: () => null }));
vi.mock("src/components/scenarios/CustomCellRenderers/ChipCellRenderer", () => ({
  getChipConfig: vi.fn(() => null),
  ChipCell: () => null,
}));
vi.mock("src/pages/dashboard/scenarios/SimulationScenarioEmptyScreen", () => ({ default: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (v) => v }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })) },
  endpoints: { scenarios: { list: "/scenarios/" } },
}));
vi.mock("react-helmet-async", () => ({ Helmet: ({ children }) => children }));

import React from "react";
import { render, screen } from "@testing-library/react";
import Scenarios from "../Scenarios";

function scenarioRow(overrides = {}) {
  return {
    id: "1", name: "Test Scenario", description: "Default description",
    agent_type: "text", dataset_rows: 5, scenario_type: "text",
    created_at: "2025-01-15T10:30:00Z", ...overrides,
  };
}

function lastColumns() {
  const c = dtMock.mock.calls;
  if (c.length === 0) return [];
  return c[c.length - 1][0]?.columns || [];
}

function descCol() {
  return lastColumns().find((c) => c.id === "description")?.cell;
}

describe("Scenarios list — description column", () => {
  beforeEach(() => {
    qState.results = [];
    qState.count = 0;
    qLoading.current = false;
    vi.clearAllMocks();
  });

  // ── Smoke ──

  it("renders without crashing", () => {
    expect(() => render(React.createElement(Scenarios, null))).not.toThrow();
  });

  it("renders the page title", () => {
    render(React.createElement(Scenarios, null));
    expect(screen.getByText("Scenarios")).toBeTruthy();
  });

  // ── Column existence / ordering ──

  it("includes a description column in DataTable columns", () => {
    qState.results = [scenarioRow()];
    qState.count = 1;

    render(React.createElement(Scenarios, null));

    expect(dtMock.mock.calls.length).toBeGreaterThan(0);
    const col = lastColumns().find((c) => c.id === "description");
    expect(col).toBeTruthy();
    expect(col.header).toBe("Description");
    expect(col.accessorKey).toBe("description");
  });

  it("positions Description between Name and Agent Type", () => {
    qState.results = [scenarioRow()];
    qState.count = 1;

    render(React.createElement(Scenarios, null));

    const ids = lastColumns().map((c) => c.id);
    expect(ids.indexOf("description")).toBeGreaterThan(ids.indexOf("name"));
    expect(ids.indexOf("description")).toBeLessThan(ids.indexOf("agentType"));
  });

  // ── Cell renderer ──

  it("cell renders the description text", () => {
    qState.results = [scenarioRow({ description: "Handles refunds" })];
    qState.count = 1;

    render(React.createElement(Scenarios, null));

    const cell = descCol();
    expect(cell).toBeTruthy();

    // Render the cell element to the DOM so we can query its text content.
    const el = cell({ getValue: () => "Handles refunds" });
    const { container } = render(el);
    expect(container.textContent).toBe("Handles refunds");
  });

  it("cell renders — for empty string", () => {
    qState.results = [scenarioRow({ description: "" })];
    qState.count = 1;

    render(React.createElement(Scenarios, null));

    const el = descCol()({ getValue: () => "" });
    const { container } = render(el);
    expect(container.textContent).toBe("—");
  });

  it("cell renders — for null", () => {
    qState.results = [scenarioRow({ description: null })];
    qState.count = 1;

    render(React.createElement(Scenarios, null));

    const el = descCol()({ getValue: () => null });
    const { container } = render(el);
    expect(container.textContent).toBe("—");
  });

  it("cell renders — for undefined", () => {
    qState.results = [scenarioRow({ description: null })];
    qState.count = 1;

    render(React.createElement(Scenarios, null));

    const el = descCol()({ getValue: () => undefined });
    const { container } = render(el);
    expect(container.textContent).toBe("—");
  });
});
