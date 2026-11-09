import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import WebhookManagementSection from "../WebhookManagementSection";

// These cases act as an Admin: declaring, editing, deleting and test-sending
// are Owner or Admin writes (useGatewayAdmin); ownerOrAdminWrites.test.jsx
// covers the other roles.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const grids = vi.hoisted(() => ({ list: [] }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grids.list.push(props);
    return <div data-testid="ag-grid" />;
  }),
}));

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar }));

const state = vi.hoisted(() => ({
  webhooks: [],
  webhooksLoading: false,
  webhooksError: null,
  refetch: null,
  events: [],
  eventsCalls: [],
  test: null,
  retry: null,
}));

const { guardrailsRead } = vi.hoisted(() => ({
  guardrailsRead: vi.fn(() => false),
}));
vi.mock("../../guardrails/hooks/useGuardrailsOff", () => ({
  useGuardrailsOff: guardrailsRead,
}));

vi.mock("../hooks/useWebhooks", () => ({
  useWebhooks: () => ({
    data: state.webhooksLoading ? undefined : state.webhooks,
    isLoading: state.webhooksLoading,
    error: state.webhooksError,
    refetch: state.refetch,
  }),
  useCreateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useTestWebhook: () => ({ mutate: state.test, isPending: false }),
  useWebhookEvents: (params) => {
    state.eventsCalls.push(params);
    const rows = params?.webhook_id
      ? state.events.filter((event) => event.webhook === params.webhook_id)
      : state.events;
    return { data: rows, isLoading: false, error: null, refetch: vi.fn() };
  },
  useRetryWebhookEvent: () => ({ mutate: state.retry, isPending: false }),
}));

const OPS = {
  id: "wh-ops",
  name: "ops-events",
  url: "https://hooks.example.com/ops",
  events: ["error.occurred", "guardrail.triggered", "budget.exceeded"],
  is_active: true,
  headers: {},
  description: "Errors, guardrail blocks and budget events to the ops sink",
  created_at: "2026-10-20T09:26:39Z",
};

const DELIVERED = {
  id: "ev-1",
  webhook: "wh-ops",
  webhook_name: "ops-events",
  event_type: "error.occurred",
  payload: {
    event: "error.occurred",
    request_id: "01M5CATF3H",
    model: "gpt-4o",
  },
  status: "delivered",
  attempts: 1,
  max_attempts: 5,
  last_attempt_at: "2026-10-20T09:38:31Z",
  last_response_code: 200,
  last_error: "",
  created_at: "2026-10-20T09:38:31Z",
};

const FAILED = {
  ...DELIVERED,
  id: "ev-2",
  event_type: "guardrail.triggered",
  status: "failed",
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

// An AG Grid column as a cell renderer sees it: its width, and the
// widthChanged event it fires when the user (or flex) resizes it.
function columnAt(width) {
  let current = width;
  const listeners = new Set();
  return {
    column: {
      getActualWidth: () => current,
      addEventListener: (type, fn) =>
        type === "widthChanged" && listeners.add(fn),
      removeEventListener: (type, fn) => listeners.delete(fn),
    },
    resize(next) {
      current = next;
      act(() => listeners.forEach((fn) => fn({ type: "widthChanged" })));
    },
  };
}
const column = (props, id) =>
  props.columnDefs.find((col) => col.field === id || col.colId === id);

beforeEach(() => {
  grids.list = [];
  state.webhooks = [OPS];
  state.webhooksLoading = false;
  state.webhooksError = null;
  state.refetch = vi.fn();
  state.events = [DELIVERED];
  state.eventsCalls = [];
  state.test = vi.fn();
  state.retry = vi.fn();
  enqueueSnackbar.mockClear();
});

describe("Webhooks: endpoints", () => {
  it("offers guardrail subscriptions without an unused gateway-config read", async () => {
    guardrailsRead.mockClear();
    renderAt("/dashboard/gateway/webhooks");
    await userEvent.click(
      screen.getByRole("button", { name: "Create webhook" }),
    );
    expect(
      screen.getByRole("checkbox", { name: /Guardrail triggered/ }),
    ).toBeEnabled();
    expect(guardrailsRead).not.toHaveBeenCalled();
  });
  it("is an FA page with one grid row per webhook", () => {
    renderAt("/dashboard/gateway/webhooks");
    expect(
      screen.getByRole("heading", { name: "Webhooks" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Endpoints/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const grid = lastGrid();
    expect(grid.rowData.map((row) => row.id)).toEqual(["wh-ops"]);
    expect(
      grid.columnDefs.map((col) => col.headerName).filter(Boolean),
    ).toEqual(
      expect.arrayContaining([
        "Webhook",
        "Endpoint",
        "Events",
        "Status",
        "Last delivery",
      ]),
    );
  });

  // Request logging sends budget.exceeded, so a webhook that subscribes to
  // it reads as one the gateway calls.
  it("shows budget.exceeded as an event this gateway sends, with no not-sent note", () => {
    renderAt("/dashboard/gateway/webhooks");
    expect(
      screen.queryByTestId("enforcement-webhooks.budgetExceeded"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/never produces/)).not.toBeInTheDocument();

    const Events = column(lastGrid(), "events").cellRenderer;
    render(
      <Events data={OPS} value={OPS.events} column={columnAt(480).column} />,
    );
    const budget = screen.getByTestId("webhook-event-budget.exceeded");
    expect(budget).toHaveAttribute("data-produced", "true");
    expect(budget).toHaveAccessibleName("budget.exceeded");
    expect(screen.getByTestId("webhook-event-error.occurred")).toHaveAttribute(
      "data-produced",
      "true",
    );
  });

  // The Events cell wrapped three chips onto three lines
  // and centred them in a 56 px row, so the first and last were cut in half.
  // It is one line: the chips that fit the column, then "+N" for the rest,
  // whose tooltip and name list them.
  it("shows one line of the event chips that fit, then +N for the rest", async () => {
    renderAt("/dashboard/gateway/webhooks");
    const Events = column(lastGrid(), "events").cellRenderer;
    const at230 = columnAt(230);
    render(<Events data={OPS} value={OPS.events} column={at230.column} />);

    const cell = screen.getByTestId("webhook-events-cell");
    expect(window.getComputedStyle(cell).flexWrap).toBe("nowrap");
    expect(screen.getByTestId("webhook-event-error.occurred")).toBeVisible();
    expect(
      screen.queryByTestId("webhook-event-guardrail.triggered"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("webhook-event-budget.exceeded"),
    ).not.toBeInTheDocument();
    const more = screen.getByTestId("webhook-events-more");
    expect(more).toHaveTextContent("+2");
    expect(more).toHaveAccessibleName(
      "2 more events: guardrail.triggered, budget.exceeded",
    );
    await userEvent.hover(more);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("budget.exceeded");
    expect(tooltip).not.toHaveTextContent("never sent");
  });

  it("shows every event chip when the column has room, with no +N", () => {
    renderAt("/dashboard/gateway/webhooks");
    const Events = column(lastGrid(), "events").cellRenderer;
    render(
      <Events data={OPS} value={OPS.events} column={columnAt(480).column} />,
    );
    for (const name of OPS.events) {
      expect(screen.getByTestId(`webhook-event-${name}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("webhook-events-more")).not.toBeInTheDocument();
  });

  it("refits its chips when the column is resized", async () => {
    renderAt("/dashboard/gateway/webhooks");
    const Events = column(lastGrid(), "events").cellRenderer;
    const resizable = columnAt(230);
    render(<Events data={OPS} value={OPS.events} column={resizable.column} />);
    expect(screen.getByTestId("webhook-events-more")).toHaveTextContent("+2");
    resizable.resize(480);
    await waitFor(() =>
      expect(
        screen.queryByTestId("webhook-events-more"),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows no not-enforced note when every subscribed event is sent", () => {
    state.webhooks = [{ ...OPS, events: ["error.occurred"] }];
    renderAt("/dashboard/gateway/webhooks");
    expect(
      screen.queryByText("Not enforced on this gateway"),
    ).not.toBeInTheDocument();
  });

  it("shows each webhook's last delivery from the delivery log", () => {
    state.events = [FAILED, DELIVERED];
    renderAt("/dashboard/gateway/webhooks");
    const row = lastGrid().rowData[0];
    expect(row.lastDelivery.id).toBe("ev-1");
    const Last = column(lastGrid(), "lastDelivery").cellRenderer;
    render(<Last data={row} />);
    expect(screen.getByText("Delivered")).toBeInTheDocument();
  });

  it("does not say None yet for a webhook that is only missing from the newest 100 deliveries", () => {
    const quiet = { ...OPS, id: "wh-quiet", name: "quiet-hook" };
    state.webhooks = [OPS, quiet];
    state.events = Array.from({ length: 100 }, (_, i) => ({
      ...DELIVERED,
      id: `ev-${i}`,
    }));
    renderAt("/dashboard/gateway/webhooks");
    const row = lastGrid().rowData.find((r) => r.id === "wh-quiet");
    const Last = column(lastGrid(), "lastDelivery").cellRenderer;
    render(<Last data={row} context={lastGrid().context} />);
    expect(screen.queryByText("None yet")).not.toBeInTheDocument();
    expect(screen.getByText("Not in the newest 100")).toBeInTheDocument();
  });

  it("sends a test delivery and reports the endpoint's answer", async () => {
    state.test = vi.fn((_id, callbacks) =>
      callbacks.onSuccess({ status_code: 200, success: true }),
    );
    renderAt("/dashboard/gateway/webhooks");
    const Actions = column(lastGrid(), "actions").cellRenderer;
    render(<Actions data={OPS} context={lastGrid().context} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Send test to ops-events" }),
    );
    expect(state.test).toHaveBeenCalledWith("wh-ops", expect.any(Object));
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Test delivered: ops-events answered 200",
      { variant: "success" },
    );
  });

  it.each([
    [null, "Connection refused"],
    [503, "Service unavailable"],
    [200, "Delivery was rejected"],
    [null, ""],
  ])(
    "reports explicit delivery failure (%s, %s) instead of success",
    async (status, error) => {
      state.test = vi.fn((_id, callbacks) =>
        callbacks.onSuccess({ success: false, status_code: status, error }),
      );
      renderAt("/dashboard/gateway/webhooks");
      const Actions = column(lastGrid(), "actions").cellRenderer;
      render(<Actions data={OPS} context={lastGrid().context} />);
      await userEvent.click(
        screen.getByRole("button", { name: "Send test to ops-events" }),
      );
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        `Test not delivered: ops-events answered ${status ?? "nothing"}${error ? `: ${error}` : ""}`,
        { variant: "warning" },
      );
      expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.any(String), {
        variant: "success",
      });
    },
  );

  it("leaves room for a sort arrow beside the Status and Created headers", () => {
    renderAt("/dashboard/gateway/webhooks");
    const grid = lastGrid();
    const min = (name) =>
      grid.columnDefs.find((col) => col.headerName === name).minWidth;
    expect(min("Status")).toBeGreaterThanOrEqual(126);
    expect(min("Created")).toBeGreaterThanOrEqual(136);
  });

  it("has one empty state with the create action", async () => {
    state.webhooks = [];
    state.events = [];
    renderAt("/dashboard/gateway/webhooks");
    expect(screen.getByText("No webhooks yet")).toBeInTheDocument();
    expect(screen.queryByTestId("ag-grid")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getAllByRole("button", { name: "Create webhook" })[1],
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("has one error state with Retry", async () => {
    state.webhooksError = new Error("Network Error");
    renderAt("/dashboard/gateway/webhooks");
    expect(screen.getByText("Couldn't load your webhooks")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.refetch).toHaveBeenCalled();
  });

  it("shows a skeleton while loading", () => {
    state.webhooksLoading = true;
    renderAt("/dashboard/gateway/webhooks");
    expect(screen.getByTestId("webhooks-loading")).toBeInTheDocument();
  });
});

describe("Webhooks: deliveries", () => {
  it("opens from its old URL and lists deliveries on the grid", () => {
    state.events = [DELIVERED, FAILED];
    renderAt("/dashboard/gateway/webhooks/delivery");
    expect(screen.getByRole("tab", { name: "Deliveries" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const grid = lastGrid();
    expect(grid.rowData.map((row) => row.id)).toEqual(["ev-1", "ev-2"]);
    expect(column(grid, "attempts").headerName).toBe("Attempts");
  });

  it("gives the Attempts and Response headers room to be read in full", () => {
    // At 1440 px every column sits at its minWidth: 100 and 110 cut the
    // headers to "Atte…" and "Resp…".
    renderAt("/dashboard/gateway/webhooks/delivery");
    const grid = lastGrid();
    // Whole while sorted too, with the arrow (sorted-header-probe-after.log).
    expect(column(grid, "attempts").minWidth).toBeGreaterThanOrEqual(144);
    expect(column(grid, "last_response_code").minWidth).toBeGreaterThanOrEqual(
      150,
    );
    expect(column(grid, "webhook_name").minWidth).toBeGreaterThanOrEqual(150);
  });

  it("shows no retry note while no delivery failed", () => {
    renderAt("/dashboard/gateway/webhooks/delivery");
    expect(screen.queryByTestId("webhook-retry-note")).not.toBeInTheDocument();
  });

  it("shows the retry note and offers Retry now in a failed delivery's drawer", async () => {
    state.events = [DELIVERED, FAILED];
    renderAt("/dashboard/gateway/webhooks/delivery");
    expect(screen.getAllByTestId("webhook-retry-note")[0]).toHaveTextContent(
      "retried automatically",
    );

    lastGrid().onRowClicked({ data: FAILED });
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("HTTP 503: unavailable")).toBeVisible();
    expect(within(drawer).getByText(/"request_id"/)).toBeInTheDocument();
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Retry now" }),
    );
    expect(state.retry).toHaveBeenCalledWith("ev-2", expect.any(Object));
  });

  // The delivery schedule runs every 30 s and sends each failed delivery once
  // its retry time (30 s, doubling) has passed; Retry resets the delivery and
  // starts it at once, the schedule picking it up if that start fails.
  it("says when a failed or retried delivery goes out: automatically on its retry time, or at once on Retry", async () => {
    state.events = [DELIVERED, FAILED];
    state.retry = vi.fn((_id, callbacks) => callbacks.onSuccess());
    renderAt("/dashboard/gateway/webhooks/delivery");
    const note = screen.getAllByTestId("webhook-retry-note")[0];
    expect(note).toHaveTextContent("30 seconds after the first failure");
    expect(note).toHaveTextContent("twice as long after each one");
    expect(note).not.toHaveTextContent("next event");

    lastGrid().onRowClicked({ data: FAILED });
    const drawer = await screen.findByRole("dialog");
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Retry now" }),
    );
    const [message] = enqueueSnackbar.mock.calls.at(-1);
    expect(message).toMatch(/within 30 seconds/);
    expect(message).not.toMatch(/next event/);
  });

  it("reads a failed attempt left pending as one that will be retried", () => {
    state.events = [
      {
        ...FAILED,
        id: "ev-3",
        status: "pending",
        attempts: 1,
        next_retry_at: "2026-10-20T09:31:00Z",
      },
    ];
    renderAt("/dashboard/gateway/webhooks/delivery");
    const Status = column(lastGrid(), "status").cellRenderer;
    render(<Status data={state.events[0]} />);
    expect(screen.getByText("Failed, will retry")).toBeInTheDocument();
  });

  it("opens a webhook's deliveries from its row", async () => {
    state.events = [DELIVERED];
    renderAt("/dashboard/gateway/webhooks");
    const Actions = column(lastGrid(), "actions").cellRenderer;
    render(<Actions data={OPS} context={lastGrid().context} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Deliveries of ops-events" }),
    );
    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/dashboard/gateway/webhooks/delivery",
      ),
    );
    expect(window.location.search).toBe("?webhook=wh-ops");
    await waitFor(() =>
      expect(state.eventsCalls).toContainEqual({ webhook_id: "wh-ops" }),
    );
  });

  it("has an empty state when nothing was delivered yet", () => {
    state.events = [];
    renderAt("/dashboard/gateway/webhooks/delivery");
    expect(screen.getByText("No deliveries yet")).toBeInTheDocument();
    // Send test writes no delivery row: its answer is only a notice.
    expect(screen.queryByText(/tries one now/i)).toBeNull();
    expect(
      screen.getByText(/send test on endpoints shows its result as a notice/i),
    ).toBeInTheDocument();
  });
});
