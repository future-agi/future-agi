// Auto routing page polish: decisions newest first by instant, times that
// name their zone, a grid that keeps Time in view when narrow, and own-model
// price lines that say what the gateway did.
import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";

import { AUTO_LOGS } from "../test-fixtures/autoLogs";
import {
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";
import { localZoneName } from "../../utils/timeZone";
import AutoRoutingPage from "./AutoRoutingPage";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// Saving auto routing is Owner or Admin only (useGatewayAdmin); these cases
// act as an Owner.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
// What the platform reports for the gateway, and whether this is a hosted
// deployment (GatewayContext's `hosted`): a hosted page calls auto at the
// gateway's own URL, a self-hosted one at the placeholder.
const gw = vi.hoisted(() => ({
  baseUrl: "http://localhost:28490/v1",
  hosted: false,
  deploymentModeConfirmed: true,
  retryDeploymentMode: vi.fn(),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: gw.baseUrl },
    hosted: gw.hosted,
    deploymentModeConfirmed: gw.deploymentModeConfirmed,
    retryDeploymentMode: gw.retryDeploymentMode,
  }),
}));
const grid = vi.hoisted(() => ({ props: null }));
// useResponsive("down", key): which breakpoints the window is below.
const below = vi.hoisted(() => ({ lg: false, md: false }));
vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: (query, key) =>
    query === "down" ? Boolean(below[key]) : false,
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

const URL = "/dashboard/gateway/models/auto";

const responses = (overrides = {}) =>
  demoResponses(endpoints, {
    [endpoints.gateway.config("default")]: {
      status: true,
      result: { routing: { auto: { preference: "balanced" } } },
    },
    [endpoints.gateway.requestLogs]: AUTO_LOGS,
    ...overrides,
  });

const open = (r = responses()) => {
  api.get.mockImplementation(answerFrom(r));
  return renderModelsAt(URL, <AutoRoutingPage />);
};

// Every request the page sent must pass the API contract.
afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});

beforeEach(() => {
  api.get.mockReset();
  grid.props = null;
  gw.baseUrl = "http://localhost:28490/v1";
  gw.hosted = false;
  gw.deploymentModeConfirmed = true;
  gw.retryDeploymentMode.mockReset();
  below.lg = false;
  below.md = false;
});

// The read is not trusted for order: rows can come back out of order, and
// one may be stamped with a UTC offset rather than Z, so the page sorts by
// instant, not by the text.
const shuffled = () => {
  const [first, second, third] = AUTO_LOGS.results;
  return {
    ...AUTO_LOGS,
    count: 3,
    results: [
      { ...first, id: "at-1730", started_at: "2026-11-11T17:30:00Z" },
      { ...second, id: "at-1709", started_at: "2026-11-11T09:09:00-08:00" },
      { ...third, id: "at-1745", started_at: "2026-11-11T17:45:00.120000Z" },
    ],
  };
};
const ownModelLog = (id, startedAt, ranked, rejected = []) => ({
  ...AUTO_LOGS.results[0],
  id,
  started_at: startedAt,
  metadata: {
    ...AUTO_LOGS.results[0].metadata,
    auto_model: ranked[0]?.model || "gpt-4o",
    auto_decision: JSON.stringify({
      model: ranked[0]?.model || "gpt-4o",
      provider: "openai",
      ranked,
      rejected,
    }),
  },
});

describe("auto routing page polish", () => {
  it("lists recent decisions newest first by when each started", async () => {
    open(responses({ [endpoints.gateway.requestLogs]: shuffled() }));
    await waitFor(() => expect(grid.props?.rowData?.length).toBe(3));
    expect(grid.props.rowData.map((d) => d.id)).toEqual([
      "at-1745",
      "at-1730",
      "at-1709",
    ]);
    const time = grid.props.columnDefs.find((c) => c.colId === "at");
    expect(time.sort).toBe("desc");
    expect(typeof time.comparator).toBe("function");
  });

  it("names the zone the decision times are in", async () => {
    open(responses({ [endpoints.gateway.requestLogs]: shuffled() }));
    expect(
      await screen.findByText(new RegExp(`Times are in ${localZoneName()}`)),
    ).toBeInTheDocument();
    const loaded = await screen.findByText(/Sample last loaded/);
    expect(loaded.closest("p, div")).toHaveTextContent(localZoneName());
  });

  it("shades the edge the decision columns run past", async () => {
    open(responses({ [endpoints.gateway.requestLogs]: shuffled() }));
    await waitFor(() => expect(grid.props?.rowData?.length).toBe(3));
    expect(typeof grid.props.onBodyScroll).toBe("function");
  });

  it("pins Time wherever the columns can run past the edge, but not on a phone", async () => {
    below.lg = true;
    open(responses({ [endpoints.gateway.requestLogs]: shuffled() }));
    await waitFor(() => expect(grid.props?.rowData?.length).toBe(3));
    expect(grid.props.columnDefs.find((c) => c.colId === "at").pinned).toBe(
      "left",
    );
    // A layout saved at another width never decides the pin: it is set
    // again for this width once the saved layout is applied.
    window.localStorage.setItem(
      "gateway-grid:gateway-auto-decisions",
      JSON.stringify([{ colId: "at", pinned: null, width: 240 }]),
    );
    const applyColumnState = vi.fn();
    act(() => grid.props.onGridReady({ api: { applyColumnState } }));
    expect(applyColumnState).toHaveBeenLastCalledWith({
      state: [{ colId: "at", pinned: "left" }],
    });
    window.localStorage.clear();
  });

  // At 1440 px the eight decision columns still run past the edge.
  it("pins Time at lg and wider too", async () => {
    open(responses({ [endpoints.gateway.requestLogs]: shuffled() }));
    await waitFor(() => expect(grid.props?.rowData?.length).toBe(3));
    expect(grid.props.columnDefs.find((c) => c.colId === "at").pinned).toBe(
      "left",
    );
  });

  it("does not pin on a phone", async () => {
    below.lg = true;
    below.md = true;
    open(responses({ [endpoints.gateway.requestLogs]: shuffled() }));
    await waitFor(() => expect(grid.props?.rowData?.length).toBe(3));
    expect(
      grid.props.columnDefs.find((c) => c.colId === "at").pinned,
    ).toBeFalsy();
    const applyColumnState = vi.fn();
    act(() => grid.props.onGridReady({ api: { applyColumnState } }));
    expect(applyColumnState).toHaveBeenLastCalledWith({
      state: [{ colId: "at", pinned: null }],
    });
  });

  // gpt-4o is on the org's own providers and has no org price. The newest
  // decision that ranked it carries the gateway's estimate, so the gateway
  // priced it; gpt-4o-mini was set aside for no price data.
  it("says what the gateway did with an own model's price, from recent decisions", async () => {
    const logs = {
      ...AUTO_LOGS,
      count: 2,
      results: [
        ownModelLog(
          "own-new",
          "2026-11-11T17:45:00Z",
          [{ model: "gpt-4o", provider: "openai", est_cost_usd: 0.0012 }],
          [{ model: "gpt-4o-mini", reason: "no price data" }],
        ),
        ownModelLog(
          "own-old",
          "2026-11-11T10:00:00Z",
          [],
          [{ model: "gpt-4o", reason: "no price data" }],
        ),
      ],
    };
    open(responses({ [endpoints.gateway.requestLogs]: logs }));
    const input = await screen.findByLabelText("Candidate models");
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.click(input);
    const listbox = await screen.findByRole("listbox");
    const option = (id) => within(listbox).getAllByText(id)[0].closest("li");
    await waitFor(() =>
      expect(option("gpt-4o")).toHaveTextContent(
        "Priced by the gateway's model database",
      ),
    );
    expect(option("gpt-4o-mini")).toHaveTextContent(
      "No price data: auto set it aside in a recent request",
    );
    expect(within(listbox).queryByText(/No price from your org/)).toBeNull();
  });
});
