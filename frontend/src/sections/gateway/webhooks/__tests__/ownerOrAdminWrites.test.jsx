import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import WebhookManagementSection from "../WebhookManagementSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../../testing/adminOnly";
import {
  CELL_PADDING_PX,
  columnOf,
  cutHeaders,
  gridBudget1440,
  minimumGridWidth,
  minimumWidth,
} from "../../logs/__tests__/gridFit";

// Creating, editing, deleting and test-sending a webhook, and retrying a
// delivery, are gateway writes: the console offers them to an organization
// Owner or Admin, as on Providers, API keys and Budgets (useGatewayAdmin).
// Anyone else sees each control disabled, with the role it needs. Reading
// webhooks and their deliveries stays open.

const grids = vi.hoisted(() => ({ list: [] }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grids.list.push(props);
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const state = vi.hoisted(() => ({
  orgLevel: 8,
  webhooks: [],
  events: [],
  test: null,
  retry: null,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));

// Whether the gateway runs guardrails (it decides the Guardrail triggered
// event's wording); these cases are about other things.
vi.mock("../../guardrails/hooks/useGuardrailsOff", () => ({
  useGuardrailsOff: () => false,
}));

vi.mock("../hooks/useWebhooks", () => ({
  useWebhooks: () => ({
    data: state.webhooks,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useTestWebhook: () => ({ mutate: state.test, isPending: false }),
  useWebhookEvents: () => ({
    data: state.events,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useRetryWebhookEvent: () => ({ mutate: state.retry, isPending: false }),
}));

const OPS = {
  id: "wh-ops",
  name: "ops-events",
  url: "https://hooks.example.com/ops",
  events: ["error.occurred"],
  is_active: true,
  headers: {},
  created_at: "2026-10-20T09:26:39Z",
};

const FAILED = {
  id: "ev-2",
  webhook: "wh-ops",
  webhook_name: "ops-events",
  event_type: "error.occurred",
  payload: { event: "error.occurred", request_id: "01M5CATF3H" },
  status: "failed",
  attempts: 1,
  max_attempts: 5,
  last_attempt_at: "2026-10-20T09:30:00Z",
  last_response_code: 503,
  last_error: "HTTP 503: unavailable",
  created_at: "2026-10-20T09:30:00Z",
};

function renderAt(path) {
  window.history.pushState({}, "", path);
  return render(
    <HelmetProvider>
      <Routes>
        <Route
          path="/dashboard/gateway/webhooks"
          element={<WebhookManagementSection />}
        />
        <Route
          path="/dashboard/gateway/webhooks/:tab"
          element={<WebhookManagementSection />}
        />
      </Routes>
    </HelmetProvider>,
  );
}

const lastGrid = () => grids.list[grids.list.length - 1];

function renderRowActions() {
  const grid = lastGrid();
  const Actions = grid.columnDefs.find(
    (col) => col.colId === "actions",
  ).cellRenderer;
  render(<Actions data={OPS} context={grid.context} />);
}

beforeEach(() => {
  grids.list = [];
  state.webhooks = [OPS];
  state.events = [FAILED];
  state.test = vi.fn();
  state.retry = vi.fn();
});

describe("Webhooks: only an Owner or Admin creates, edits, deletes, test-sends or retries", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Create webhook, Send test, Edit and Delete disabled, with the role they need",
    (_who, level) => {
      state.orgLevel = level;
      renderAt("/dashboard/gateway/webhooks");
      expectAdminOnly(screen.getByRole("button", { name: "Create webhook" }));
      renderRowActions();
      expectAdminOnly(
        screen.getByRole("button", { name: "Send test to ops-events" }),
      );
      expectAdminOnly(screen.getByRole("button", { name: "Edit ops-events" }));
      expectAdminOnly(
        screen.getByRole("button", { name: "Delete ops-events" }),
      );
      // Reading its deliveries stays open.
      expectEditable(
        screen.getByRole("button", { name: "Deliveries of ops-events" }),
      );
      expect(state.test).not.toHaveBeenCalled();
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s is not offered Create webhook from the empty list",
    (_who, level) => {
      state.orgLevel = level;
      state.webhooks = [];
      renderAt("/dashboard/gateway/webhooks");
      // Only the page's own Create webhook, disabled: the empty list offers
      // none.
      const creates = screen.getAllByRole("button", { name: "Create webhook" });
      expect(creates).toHaveLength(1);
      expectAdminOnly(creates[0]);
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees a failed delivery's Retry now disabled",
    async (_who, level) => {
      state.orgLevel = level;
      renderAt("/dashboard/gateway/webhooks/delivery");
      lastGrid().onRowClicked({ data: FAILED });
      const drawer = await screen.findByRole("dialog");
      expectAdminOnly(
        within(drawer).getByRole("button", { name: "Retry now" }),
      );
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can create, test-send, edit and delete",
    (_who, level) => {
      state.orgLevel = level;
      renderAt("/dashboard/gateway/webhooks");
      expectEditable(screen.getByRole("button", { name: "Create webhook" }));
      renderRowActions();
      for (const name of [
        "Send test to ops-events",
        "Edit ops-events",
        "Delete ops-events",
      ]) {
        expectEditable(screen.getByRole("button", { name }));
      }
    },
  );
});

// Each row's Send test, Deliveries, Edit and Delete sit whole inside the
// actions column at 1440 px, so each one shows its own tooltip on hover,
// Delete's "Owner or Admin only" included. Widths, not browser-measured:
// Send test is a small outlined button (12 px Inter 500 "Send test", 54.3 px
// from the font's advance widths, rounded up plus 1; 24 px padding each side;
// a 1 px border), each icon button is a 16 px glyph with 5 px padding, and
// the row keeps 4 px between controls.
const SEND_TEST_PX = 56 + 24 * 2 + 2;
const ICON_BUTTON_PX = 16 + 5 * 2;
const ROW_GAP_PX = 4;
const ROW_ACTIONS_PX =
  SEND_TEST_PX + 3 * ICON_BUTTON_PX + 3 * ROW_GAP_PX + CELL_PADDING_PX;

describe("Webhooks grid at 1440 px", () => {
  it("holds every row control inside the actions column, with every column on screen", () => {
    state.orgLevel = 3;
    renderAt("/dashboard/gateway/webhooks");
    const grid = lastGrid();
    expect(minimumWidth(columnOf(grid, "actions"))).toBeGreaterThanOrEqual(
      ROW_ACTIONS_PX,
    );
    expect(minimumGridWidth(grid)).toBeLessThanOrEqual(gridBudget1440());
    expect(cutHeaders(grid)).toEqual([]);
  });
});
