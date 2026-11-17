import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { SnackbarProvider, closeSnackbar } from "notistack";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import {
  WORKSPACE_RECOVERY_HANDLED,
  holdWorkspaceRefusals,
} from "src/contexts/workspaceRefusalHold";
import { handleError } from "src/utils/queryErrorHandler";

import WebhookManagementSection from "../WebhookManagementSection";

// These cases act as an Admin: declaring, editing, deleting and test-sending
// are Owner or Admin writes (useGatewayAdmin); ownerOrAdminWrites.test.jsx
// covers the other roles.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const grids = vi.hoisted(() => ({ list: [], renderActions: false }));
/* eslint-disable react/prop-types -- AG Grid test double */
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grids.list.push(props);
    const Actions = props.columnDefs.find(
      (col) => col.colId === "actions",
    )?.cellRenderer;
    return (
      <div data-testid="ag-grid">
        {grids.renderActions &&
          Actions &&
          props.rowData?.map((row) => (
            <Actions key={row.id} data={row} context={props.context} />
          ))}
      </div>
    );
  }),
}));
/* eslint-enable react/prop-types */

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  // These exports are rebound when SnackbarProvider mounts; look them up at
  // call time so the toast-dismissal assertion exercises a real snackbar.
  enqueueSnackbar.mockImplementation((...args) =>
    actual.enqueueSnackbar(...args),
  );
  return {
    ...actual,
    enqueueSnackbar,
    closeSnackbar: (...args) => actual.closeSnackbar(...args),
  };
});

const http = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, ...http } };
});

const state = vi.hoisted(() => ({
  webhooks: [],
  webhooksLoading: false,
  webhooksError: null,
  refetch: null,
  events: [],
  eventsCalls: [],
  create: null,
  update: null,
  test: null,
  retry: null,
}));

const { guardrailsRead } = vi.hoisted(() => ({
  guardrailsRead: vi.fn(() => false),
}));
vi.mock("../../guardrails/hooks/useGuardrailsOff", () => ({
  useGuardrailsOff: guardrailsRead,
}));

vi.mock("../hooks/useWebhooks", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    useWebhooks: () => ({
      data: state.webhooksLoading ? undefined : state.webhooks,
      isLoading: state.webhooksLoading,
      error: state.webhooksError,
      refetch: state.refetch,
    }),
    useCreateWebhook: (options) =>
      state.realSaveHooks
        ? actual.useCreateWebhook(options)
        : { mutate: state.create, isPending: false },
    useUpdateWebhook: (options) =>
      state.realSaveHooks
        ? actual.useUpdateWebhook(options)
        : { mutate: state.update, isPending: false },
    useDeleteWebhook: () => ({ mutate: vi.fn(), isPending: false }),
    useTestWebhook: () =>
      state.realDeliveryHooks
        ? actual.useTestWebhook()
        : { mutate: state.test, isPending: false },
    useWebhookEvents: (params) => {
      if (state.realDeliveryHooks) return actual.useWebhookEvents(params);
      state.eventsCalls.push(params);
      const rows = params?.webhook_id
        ? state.events.filter((event) => event.webhook === params.webhook_id)
        : state.events;
      return { data: rows, isLoading: false, error: null, refetch: vi.fn() };
    },
    useRetryWebhookEvent: () => ({ mutate: state.retry, isPending: false }),
  };
});

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

// A delivery whose attempt failed and that the delivery schedule sends again
// at next_retry_at ("Failed, will retry").
const RETRYING = {
  ...DELIVERED,
  id: "ev-3",
  event_type: "request.completed",
  status: "pending",
  attempts: 1,
  last_response_code: 503,
  last_error: "HTTP 503: unavailable",
  next_retry_at: "2026-10-20T09:31:00Z",
  created_at: "2026-10-20T09:20:00Z",
};

function renderAt(path, client = new QueryClient()) {
  window.history.pushState({}, "", path);
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
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
        </HelmetProvider>
      </SnackbarProvider>
    </QueryClientProvider>,
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
  grids.renderActions = false;
  state.realDeliveryHooks = false;
  state.realSaveHooks = false;
  http.get.mockReset();
  http.post.mockReset();
  http.patch.mockReset();
  state.webhooks = [OPS];
  state.webhooksLoading = false;
  state.webhooksError = null;
  state.refetch = vi.fn();
  state.events = [DELIVERED];
  state.eventsCalls = [];
  state.create = vi.fn();
  state.update = vi.fn();
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

  it("says on the webhook's row why its last delivery failed", () => {
    const reason =
      "The webhook URL answered with a redirect. Save the final URL.";
    state.events = [
      {
        ...FAILED,
        id: "ev-3",
        last_response_code: 301,
        last_error: reason,
        created_at: "2026-10-20T09:40:00Z",
      },
      DELIVERED,
    ];
    renderAt("/dashboard/gateway/webhooks");
    const row = lastGrid().rowData[0];
    expect(row.lastDelivery.id).toBe("ev-3");
    const Last = column(lastGrid(), "lastDelivery").cellRenderer;
    render(<Last data={row} />);
    const line = screen.getByText(reason);
    expect(line).toHaveAttribute("title", reason);
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
        `Test not delivered: ops-events answered ${status == null ? "nothing" : `HTTP ${status}`}${error ? `: ${error}` : ""}`,
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

// The server refuses a webhook's name with a 400 whose body lists the
// refused fields and their messages (tfc/utils/api_errors.py
// build_error_envelope): one line naming them all, and each field's own
// messages under details. axios.js rejects with that body and its status.
const NAME_RULE = "Letters, digits, _ and - only.";
const NAME_REFUSED =
  "Name can only contain letters, numbers, hyphens, and underscores";
const refusal = (details) => {
  const text = Object.entries(details)
    .map(([field, messages]) => `${field}: ${messages.join(", ")}`)
    .join("; ");
  return {
    status: false,
    detail: text,
    message: text,
    error: text,
    result: text,
    attr: Object.keys(details)[0],
    details,
    statusCode: 400,
  };
};
const refuse = (body) =>
  vi.fn((_payload, callbacks) => callbacks.onError(body));

// A save whose refusal comes back only when the test calls answer().
function heldRefusal(body) {
  const save = { answer: null };
  save.mutate = vi.fn((_payload, callbacks) => {
    save.answer = () => act(() => callbacks.onError(body));
  });
  return save;
}

// Fills the Create dialog of the page on screen (this name, an HTTPS URL and
// one event) and clicks Create.
async function createFromDialog(name) {
  await userEvent.click(screen.getByRole("button", { name: "Create webhook" }));
  const dialog = screen.getByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText(/Name/), name);
  await userEvent.type(
    within(dialog).getByLabelText(/^URL/),
    "https://hooks.example.com/ops",
  );
  await userEvent.click(
    within(dialog).getByRole("checkbox", { name: /Error occurred/ }),
  );
  await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
  return dialog;
}

// Creates a webhook from the page: this name, an HTTPS URL and one event.
async function submitNewWebhook(name, client) {
  renderAt("/dashboard/gateway/webhooks", client);
  return createFromDialog(name);
}

// A refusal of the name alone shows on the Name field of the dialog the save
// came from, from the moment it comes back until the name is next edited,
// and takes focus there. Any other refusal, or one that comes back once that
// dialog is closed or another one is open, is a snackbar: never lost.
describe("Webhooks: a save the server refuses", () => {
  it("shows the server's refusal of the name on the Name field and moves focus to it, until the name is next edited", async () => {
    state.create = refuse(refusal({ name: [NAME_REFUSED] }));
    const dialog = await submitNewWebhook("ops_events");
    expect(state.create).toHaveBeenCalledTimes(1);
    const name = within(dialog).getByLabelText(/Name/);
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByText(NAME_REFUSED)).toBeInTheDocument();
    expect(name).toHaveAccessibleDescription(NAME_REFUSED);
    // The dialog stayed open, the name as it was typed.
    expect(name).toHaveValue("ops_events");
    expect(name).toHaveFocus();
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    await userEvent.type(name, "2");
    expect(within(dialog).queryByText(NAME_REFUSED)).not.toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "false");
    expect(within(dialog).getByText(NAME_RULE)).toBeInTheDocument();
    expect(name).toHaveAccessibleDescription(NAME_RULE);
  });

  it("shows the server's refusal of the name on the Name field when an edit is refused", async () => {
    state.update = refuse(refusal({ name: [NAME_REFUSED] }));
    grids.renderActions = true;
    renderAt("/dashboard/gateway/webhooks");
    await userEvent.click(
      screen.getByRole("button", { name: "Edit ops-events" }),
    );
    const dialog = screen.getByRole("dialog");
    const name = within(dialog).getByLabelText(/Name/);
    await userEvent.clear(name);
    await userEvent.type(name, "ops_events_2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(state.update).toHaveBeenCalledWith(
      expect.objectContaining({ id: "wh-ops", name: "ops_events_2" }),
      expect.any(Object),
    );
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByText(NAME_REFUSED)).toBeInTheDocument();
    expect(name).toHaveAccessibleDescription(NAME_REFUSED);
    // The dialog stayed open, the name as it was typed.
    expect(name).toHaveValue("ops_events_2");
    expect(name).toHaveFocus();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("shows a refusal of the name that comes back after the name was edited", async () => {
    const save = heldRefusal(refusal({ name: [NAME_REFUSED] }));
    state.create = save.mutate;
    const dialog = await submitNewWebhook("ops_events");
    const name = within(dialog).getByLabelText(/Name/);
    await userEvent.type(name, "2");
    save.answer();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByText(NAME_REFUSED)).toBeInTheDocument();
    expect(name).toHaveAccessibleDescription(NAME_REFUSED);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("keeps the snackbar for a refusal of another field", async () => {
    state.create = refuse(refusal({ url: ["Enter a valid URL."] }));
    const dialog = await submitNewWebhook("ops_events");
    expect(enqueueSnackbar).toHaveBeenCalledWith("url: Enter a valid URL.", {
      variant: "error",
    });
    expect(within(dialog).getByLabelText(/Name/)).toHaveAttribute(
      "aria-invalid",
      "false",
    );
  });

  it("keeps the snackbar for a refusal of the name and another field", async () => {
    state.create = refuse(
      refusal({ name: [NAME_REFUSED], url: ["Enter a valid URL."] }),
    );
    const dialog = await submitNewWebhook("ops_events");
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      `name: ${NAME_REFUSED}; url: Enter a valid URL.`,
      { variant: "error" },
    );
    expect(within(dialog).getByLabelText(/Name/)).toHaveAttribute(
      "aria-invalid",
      "false",
    );
  });

  // Only a 400 is a refusal of what was typed: a server error keeps the
  // snackbar even when its body names the name.
  it("keeps the snackbar for a server error whose body names the name", async () => {
    state.create = refuse({
      ...refusal({ name: [NAME_REFUSED] }),
      statusCode: 500,
    });
    const dialog = await submitNewWebhook("ops_events");
    expect(enqueueSnackbar).toHaveBeenCalledWith(`name: ${NAME_REFUSED}`, {
      variant: "error",
    });
    expect(within(dialog).getByLabelText(/Name/)).toHaveAttribute(
      "aria-invalid",
      "false",
    );
  });

  it("keeps the snackbar for a refused name that comes back after the dialog closed", async () => {
    const save = heldRefusal(refusal({ name: [NAME_REFUSED] }));
    state.create = save.mutate;
    const dialog = await submitNewWebhook("ops_events");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    save.answer();
    expect(enqueueSnackbar).toHaveBeenCalledWith(`name: ${NAME_REFUSED}`, {
      variant: "error",
    });
  });

  it("keeps the snackbar for a refused name that comes back once another dialog is open", async () => {
    const save = heldRefusal(refusal({ name: [NAME_REFUSED] }));
    state.create = save.mutate;
    grids.renderActions = true;
    const created = await submitNewWebhook("ops_events");
    await userEvent.click(
      within(created).getByRole("button", { name: "Cancel" }),
    );
    // The page behind the closed dialog is reachable again.
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit ops-events" }),
    );
    const edit = screen.getByRole("dialog");
    save.answer();
    expect(enqueueSnackbar).toHaveBeenCalledWith(`name: ${NAME_REFUSED}`, {
      variant: "error",
    });
    expect(within(edit).getByLabelText(/Name/)).toHaveAttribute(
      "aria-invalid",
      "false",
    );
    expect(within(edit).queryByText(NAME_REFUSED)).not.toBeInTheDocument();
  });
});

// The app's QueryClient (src/app.jsx): a failed save reaches the app-wide
// handleError on the MutationCache first, then the page's own onError. The
// handler is wrapped (it still runs) so a case can show it ran before it
// checks that no toast came from it.
let cacheError;
const appClient = () => {
  cacheError = vi.fn(handleError);
  return new QueryClient({
    mutationCache: new MutationCache({ onError: cacheError }),
  });
};

// Both have told a failed save once it is no longer pending: the page's
// onError runs in the same step that ends it, after the app-wide handler.
const settled = (client) => waitFor(() => expect(client.isMutating()).toBe(0));

// The server fails (500). The page says the server's words; the app-wide
// toast says a server's words only for a refusal (400, 404, 409 or 422), so
// it says "Something went wrong".
const SERVER_ERROR = {
  status: false,
  result: "The webhook could not be saved.",
  statusCode: 500,
};
const APP_FALLBACK = "Something went wrong";

// The gateway's own sign-in is refused: axios.js passes this 401 on as it
// came, and the app-wide handler says what to do in its own words, never the
// server's.
const GATEWAY_REFUSAL = {
  message: "Request failed with status code 401",
  response: {
    status: 401,
    data: {
      code: "gateway_request_rejected",
      message: "Upstream credential rejected",
    },
  },
};
const GATEWAY_REMEDY =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";

// A workspace the person can no longer use: axios.js marks the refusal that
// the running workspace recovery claims (it explains it with its own line).
const WORKSPACE_REFUSAL = {
  status: false,
  code: "workspace_unavailable",
  message: "This workspace is no longer available.",
  result: "This workspace is no longer available.",
  statusCode: 403,
  handledBy: WORKSPACE_RECOVERY_HANDLED,
};

// A rate limit that also carries the workspace recovery's mark. No server
// answers a workspace refusal with 429 today; the case pins that a rate limit
// stays with the page whatever else marks it, since the app-wide handler
// stops at a rate limit before any words of its own.
const RATE_LIMITED = {
  status: false,
  code: "workspace_unavailable",
  result: "Rate limit reached.",
  statusCode: 429,
  handledBy: WORKSPACE_RECOVERY_HANDLED,
};

// Edits the webhook on the page (renamed), with the row actions shown, and
// clicks Save. Returns the dialog.
async function editFromDialog(client) {
  grids.renderActions = true;
  renderAt("/dashboard/gateway/webhooks", client);
  await userEvent.click(
    screen.getByRole("button", { name: "Edit ops-events" }),
  );
  const dialog = screen.getByRole("dialog");
  const name = within(dialog).getByLabelText(/Name/);
  await userEvent.clear(name);
  await userEvent.type(name, "ops_events_2");
  await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
  return dialog;
}

// A save whose request stays open until the test calls the returned fail().
function heldRequest(request) {
  const held = { fail: null };
  request.mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        held.fail = reject;
      }),
  );
  return held;
}

// A failed save is told once, through the real save hooks and the app's
// error handler: a refusal of the name on the open dialog by the Name field
// alone; any other failure while the page is open by the page's snackbar
// alone; a lapsed sign-in or workspace by the app-wide handler alone; and a
// failure that comes back once the page was left by the app-wide toast.
describe("Webhooks: a failed save is told once", () => {
  beforeEach(() => {
    state.realSaveHooks = true;
  });

  it("shows a refusal of the name on the open dialog's Name field, with no toast", async () => {
    const rejection = refusal({ name: [NAME_REFUSED] });
    http.post.mockRejectedValueOnce(rejection);
    const client = appClient();
    const dialog = await submitNewWebhook("ops_events", client);
    await settled(client);
    const name = within(dialog).getByLabelText(/Name/);
    await waitFor(() => expect(name).toHaveAttribute("aria-invalid", "true"));
    expect(name).toHaveAccessibleDescription(NAME_REFUSED);
    expect(http.post).toHaveBeenCalledTimes(1);
    // The app-wide handler ran and said nothing.
    expect(cacheError).toHaveBeenCalledTimes(1);
    expect(cacheError.mock.calls[0][0]).toBe(rejection);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("shows a refusal of an edit's name on its Name field, with no toast", async () => {
    const rejection = refusal({ name: [NAME_REFUSED] });
    http.patch.mockRejectedValueOnce(rejection);
    const client = appClient();
    const dialog = await editFromDialog(client);
    await settled(client);
    const name = within(dialog).getByLabelText(/Name/);
    await waitFor(() => expect(name).toHaveAttribute("aria-invalid", "true"));
    expect(name).toHaveAccessibleDescription(NAME_REFUSED);
    expect(http.patch).toHaveBeenCalledTimes(1);
    expect(cacheError).toHaveBeenCalledTimes(1);
    expect(cacheError.mock.calls[0][0]).toBe(rejection);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("says another failure of an edit once, with the page's snackbar", async () => {
    http.patch.mockRejectedValueOnce(SERVER_ERROR);
    const client = appClient();
    await editFromDialog(client);
    await settled(client);
    expect(http.patch).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(SERVER_ERROR.result, {
      variant: "error",
    });
  });

  // The request is still open when the page re-renders the pending save, as
  // it is over a real network.
  it("says another failure once when it comes back after the save was pending", async () => {
    const held = heldRequest(http.post);
    const client = appClient();
    const dialog = await submitNewWebhook("ops_events", client);
    await within(dialog).findByRole("button", { name: "Saving…" });
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    await act(async () => {});
    await act(async () => held.fail(SERVER_ERROR));
    await settled(client);
    expect(cacheError).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(SERVER_ERROR.result, {
      variant: "error",
    });
  });

  it("shows a refusal of the name that comes back after the save was pending on the Name field alone", async () => {
    const held = heldRequest(http.post);
    const rejection = refusal({ name: [NAME_REFUSED] });
    const client = appClient();
    const dialog = await submitNewWebhook("ops_events", client);
    await within(dialog).findByRole("button", { name: "Saving…" });
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    await act(async () => {});
    await act(async () => held.fail(rejection));
    await settled(client);
    const name = within(dialog).getByLabelText(/Name/);
    await waitFor(() => expect(name).toHaveAttribute("aria-invalid", "true"));
    expect(name).toHaveAccessibleDescription(NAME_REFUSED);
    expect(cacheError).toHaveBeenCalledTimes(1);
    expect(cacheError.mock.calls[0][0]).toBe(rejection);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("says a rate limit on the open page with the page's snackbar, once, whatever else marks it", async () => {
    http.post.mockRejectedValueOnce(RATE_LIMITED);
    const client = appClient();
    await submitNewWebhook("ops_events", client);
    await settled(client);
    expect(cacheError).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(RATE_LIMITED.result, {
      variant: "error",
    });
  });

  it("says another failure on the open page once, with the page's snackbar", async () => {
    http.post.mockRejectedValueOnce(SERVER_ERROR);
    const client = appClient();
    await submitNewWebhook("ops_events", client);
    await settled(client);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(SERVER_ERROR.result, {
      variant: "error",
    });
  });

  it("leaves the gateway's sign-in refusal to the app-wide handler: its words, once", async () => {
    http.post.mockRejectedValueOnce(GATEWAY_REFUSAL);
    const client = appClient();
    await submitNewWebhook("ops_events", client);
    await settled(client);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(GATEWAY_REMEDY, {
      variant: "error",
    });
    // The dialog stays open, with what the person typed.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("leaves a lapsed workspace to the app-wide handler, never the page's snackbar", async () => {
    http.post.mockRejectedValueOnce(WORKSPACE_REFUSAL);
    const client = appClient();
    // The workspace recovery is running when the save fails.
    const release = holdWorkspaceRefusals();
    try {
      await submitNewWebhook("ops_events", client);
      await settled(client);
      // Its own line explains the refusal: nothing else says it.
      expect(cacheError).toHaveBeenCalledTimes(1);
      expect(cacheError.mock.calls[0][0]).toBe(WORKSPACE_REFUSAL);
      expect(enqueueSnackbar).not.toHaveBeenCalled();
      // It ends with no line of its own: the app-wide toast is shown after
      // all, once.
      act(() => release(0, { unexplained: true }));
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledWith(APP_FALLBACK, {
        variant: "error",
      });
    } finally {
      release();
      // The next recovery starts from an explained end, as before this case.
      holdWorkspaceRefusals()();
    }
  });

  it("leaves a lapsed workspace to the recovery's own line when the recovery explains it", async () => {
    http.post.mockRejectedValueOnce(WORKSPACE_REFUSAL);
    const client = appClient();
    const release = holdWorkspaceRefusals();
    try {
      await submitNewWebhook("ops_events", client);
      await settled(client);
      expect(cacheError).toHaveBeenCalledTimes(1);
      expect(cacheError.mock.calls[0][0]).toBe(WORKSPACE_REFUSAL);
      // The recovery ends with a line of its own: nothing else says it.
      act(() => release());
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    } finally {
      release();
    }
  });

  it("still tells a lapsed workspace once when a later save failed on the open page in between", async () => {
    http.post
      .mockRejectedValueOnce(WORKSPACE_REFUSAL)
      .mockRejectedValueOnce(SERVER_ERROR);
    const client = appClient();
    const release = holdWorkspaceRefusals();
    try {
      const dialog = await submitNewWebhook("ops_events", client);
      await settled(client);
      expect(cacheError).toHaveBeenCalledTimes(1);
      expect(cacheError.mock.calls[0][0]).toBe(WORKSPACE_REFUSAL);
      expect(enqueueSnackbar).not.toHaveBeenCalled();
      // The person saves again from the same dialog; this one fails while
      // the page is open, and the page says it.
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Create" }),
      );
      await settled(client);
      expect(http.post).toHaveBeenCalledTimes(2);
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledWith(SERVER_ERROR.result, {
        variant: "error",
      });
      // The recovery ends with no line of its own: the first save is told
      // after all, once, by the app-wide toast.
      act(() => release(0, { unexplained: true }));
      expect(enqueueSnackbar).toHaveBeenCalledTimes(2);
      expect(enqueueSnackbar).toHaveBeenLastCalledWith(APP_FALLBACK, {
        variant: "error",
      });
    } finally {
      release();
      holdWorkspaceRefusals()();
    }
  });

  it("says a failure that comes back once the page was left with the app-wide toast, once", async () => {
    let fail;
    http.post.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          fail = reject;
        }),
    );
    const client = appClient();
    const view = renderAt("/dashboard/gateway/webhooks", client);
    await createFromDialog("ops_events");
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    // The person leaves the page before the answer comes back; the app's
    // providers stay.
    view.rerender(
      <QueryClientProvider client={client}>
        <SnackbarProvider>
          <p>Another page</p>
        </SnackbarProvider>
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => fail(SERVER_ERROR));
    await settled(client);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(APP_FALLBACK, {
      variant: "error",
    });
  });
});

describe("Send test: receiver proof and delivery cache", () => {
  const eventsKey = (params = {}) => ["agentcc-webhook-events", params];
  const filteredKey = eventsKey({ webhook_id: OPS.id });
  const otherKey = eventsKey({ webhook_id: "wh-other" });

  function setup() {
    state.realDeliveryHooks = true;
    grids.renderActions = true;
    // A test send must never inherit a client's automatic mutation retry policy.
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: 2, retryDelay: 0 },
      },
    });
    client.setQueryData(eventsKey(), []);
    client.setQueryData(filteredKey, []);
    client.setQueryData(otherKey, []);
    renderAt("/dashboard/gateway/webhooks", client);
    return client;
  }

  it.each([
    [200, true, "", "Delivered"],
    [204, true, "", "Delivered"],
    [500, false, "Receiver unavailable", "Not delivered"],
    [500, true, "Receiver unavailable", "Not delivered"],
    [302, false, "Redirect refused", "Not delivered"],
    [null, false, "Connection refused", "Not delivered"],
  ])(
    "retains receiver %s (%s) after toast dismissal and refreshes matching deliveries",
    async (code, success, error, label) => {
      const delivery = {
        ...DELIVERED,
        id: "test-delivery",
        event_type: "test",
        status: label === "Delivered" ? "delivered" : "failed",
        last_response_code: code,
        last_error: error,
      };
      http.post.mockResolvedValue({
        data: { success: true, result: { status_code: code, success, error } },
      });
      http.get.mockResolvedValue({ data: { result: [delivery] } });
      const client = setup();
      expect(http.get).not.toHaveBeenCalled();

      await userEvent.click(
        screen.getByRole("button", { name: "Send test to ops-events" }),
      );
      await waitFor(() =>
        expect(lastGrid().rowData[0].lastDelivery).toEqual(delivery),
      );
      expect(http.post).toHaveBeenCalledTimes(1);
      expect(http.post.mock.calls[0][0]).toMatch(/webhooks\/wh-ops\/test\/?$/);
      expect(http.get).toHaveBeenCalledWith(expect.any(String), { params: {} });
      expect(client.getQueryState(filteredKey).isInvalidated).toBe(true);
      expect(client.getQueryState(otherKey).isInvalidated).toBe(false);
      expect(client.getQueryData(otherKey)).toEqual([]);

      const outcome = screen.getByRole("status", {
        name: "Latest webhook test",
      });
      expect(within(outcome).getByText(label)).toBeVisible();
      expect(outcome).toHaveTextContent(OPS.name);
      expect(outcome).toHaveTextContent(OPS.url);
      expect(outcome).toHaveTextContent(
        code == null ? "No receiver response" : `HTTP ${code}`,
      );
      if (error) expect(outcome).toHaveTextContent(error);
      const toast = enqueueSnackbar.mock.calls.at(-1)[0];
      expect(screen.getByText(toast)).toBeVisible();
      act(() => closeSnackbar());
      await waitFor(() =>
        expect(screen.queryByText(toast)).not.toBeInTheDocument(),
      );
      expect(within(outcome).getByText(label)).toBeVisible();
      if (error) expect(outcome).toHaveTextContent(error);

      await userEvent.click(
        within(outcome).getByRole("button", { name: "View deliveries" }),
      );
      await waitFor(() =>
        expect(window.location.search).toBe("?webhook=wh-ops"),
      );
      await waitFor(() =>
        expect(http.get).toHaveBeenCalledWith(expect.any(String), {
          params: { webhook_id: "wh-ops" },
        }),
      );
      await waitFor(() => expect(lastGrid().rowData).toEqual([delivery]));
      expect(http.post).toHaveBeenCalledTimes(1);
      client.clear();
    },
  );

  it("does not retry a pre-dispatch rejection or invent a receiver result or delivery", async () => {
    http.post.mockRejectedValue({
      response: {
        status: 400,
        data: { result: "Test rejected before sending" },
      },
    });
    const client = setup();
    await userEvent.click(
      screen.getByRole("button", { name: "Send test to ops-events" }),
    );
    const outcome = await screen.findByRole("status", {
      name: "Latest webhook test",
    });
    await waitFor(() =>
      expect(outcome).toHaveTextContent("Test result unavailable"),
    );
    expect(outcome).toHaveTextContent("Test rejected before sending");
    expect(outcome).toHaveTextContent(
      "Check deliveries before sending another test.",
    );
    expect(
      within(outcome).queryByText(/^(Delivered|Not delivered)$/),
    ).not.toBeInTheDocument();
    expect(outcome).not.toHaveTextContent("HTTP 400");
    expect(client.getQueryData(eventsKey())).toEqual([]);
    expect(lastGrid().rowData[0].lastDelivery).toBeNull();
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.get).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.any(String), {
      variant: "success",
    });
    client.clear();
  });

  it("replaces the previous outcome while an explicit second test is pending", async () => {
    http.post.mockResolvedValueOnce({
      data: { result: { status_code: 204, success: true } },
    });
    http.get.mockResolvedValue({ data: { result: [] } });
    const client = setup();
    const send = () =>
      screen.getByRole("button", { name: "Send test to ops-events" });
    await userEvent.click(send());
    const outcome = await screen.findByRole("status", {
      name: "Latest webhook test",
    });
    await waitFor(() =>
      expect(within(outcome).getByText("Delivered")).toBeVisible(),
    );
    let finish;
    http.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await userEvent.click(send());
    expect(send()).toBeDisabled();
    expect(outcome).toHaveTextContent("Sending test…");
    expect(outcome).not.toHaveTextContent("HTTP 204");
    expect(within(outcome).queryByText("Delivered")).not.toBeInTheDocument();
    expect(http.post).toHaveBeenCalledTimes(2);
    await act(async () =>
      finish({ data: { result: { status_code: 500, success: false } } }),
    );
    await waitFor(() => expect(outcome).toHaveTextContent("Not delivered"));
    expect(outcome).toHaveTextContent("HTTP 500");
    expect(send()).toBeEnabled();
    expect(http.post).toHaveBeenCalledTimes(2);
    client.clear();
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
    expect(
      screen.queryByTestId("webhook-not-retried-note"),
    ).not.toBeInTheDocument();
  });

  // A Failed delivery (a failed Send test, or a redirect, recorded once) is
  // never sent again by itself: only Retry now sends it, at once, the
  // schedule picking it up within 30 s if that start fails.
  it("says a Failed delivery is not retried automatically, and offers Retry now in its drawer", async () => {
    state.events = [DELIVERED, FAILED];
    state.retry = vi.fn((_id, callbacks) => callbacks.onSuccess());
    renderAt("/dashboard/gateway/webhooks/delivery");
    expect(screen.queryByTestId("webhook-retry-note")).not.toBeInTheDocument();
    expect(screen.getByTestId("webhook-not-retried-note")).toHaveTextContent(
      "Deliveries marked Failed or Gave up are not retried automatically.",
    );

    lastGrid().onRowClicked({ data: FAILED });
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByTestId("webhook-not-retried-note"),
    ).toHaveTextContent(
      "Not retried automatically. Fix what the error names, then use Retry now",
    );
    expect(
      within(drawer).queryByTestId("webhook-retry-note"),
    ).not.toBeInTheDocument();
    expect(within(drawer).getByText("HTTP 503: unavailable")).toBeVisible();
    expect(within(drawer).getByText(/"request_id"/)).toBeInTheDocument();
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Retry now" }),
    );
    expect(state.retry).toHaveBeenCalledWith("ev-2", expect.any(Object));
    const [message] = enqueueSnackbar.mock.calls.at(-1);
    expect(message).toMatch(/within 30 seconds/);
    expect(message).not.toMatch(/next event/);
  });

  // The delivery schedule runs every 30 s and sends a delivery awaiting its
  // retry once its retry time (30 s, doubling) has passed.
  it("says when a delivery awaiting its retry goes out: automatically, on its retry time", async () => {
    state.events = [DELIVERED, RETRYING, FAILED];
    renderAt("/dashboard/gateway/webhooks/delivery");
    const note = screen.getByTestId("webhook-retry-note");
    expect(note).toHaveTextContent(
      "A delivery marked Failed, will retry is sent again automatically",
    );
    expect(note).toHaveTextContent("30 seconds after the first failure");
    expect(note).toHaveTextContent("twice as long after each one");
    expect(note).not.toHaveTextContent("next event");
    // Retry now is offered only for Failed and Gave up deliveries.
    expect(note).not.toHaveTextContent("Retry now");
    expect(screen.getByTestId("webhook-not-retried-note")).toBeVisible();

    lastGrid().onRowClicked({ data: RETRYING });
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByTestId("webhook-retry-note")).toHaveTextContent(
      "sent again automatically",
    );
    expect(
      within(drawer).queryByTestId("webhook-not-retried-note"),
    ).not.toBeInTheDocument();
    expect(
      within(drawer).queryByRole("button", { name: "Retry now" }),
    ).not.toBeInTheDocument();
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
    expect(screen.queryByText(/tries one now/i)).toBeNull();
    expect(
      screen.getByText(/Recorded Send test results appear here too/),
    ).toHaveTextContent("Tests rejected before sending may not appear.");
  });
});
