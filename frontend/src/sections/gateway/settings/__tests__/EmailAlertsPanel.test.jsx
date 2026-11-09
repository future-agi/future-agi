import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import EmailAlertsCard from "../EmailAlertsCard";

const grid = vi.hoisted(() => ({ props: null }));
const dialog = vi.hoisted(() => ({ props: null }));
const state = vi.hoisted(() => ({
  alerts: [],
  isLoading: false,
  remove: vi.fn(),
  update: vi.fn(),
  test: vi.fn(),
  snackbars: [],
}));

vi.mock("../../components/frame", async () => {
  const actual = await vi.importActual("../../components/frame");
  return {
    ...actual,
    GatewayGrid: (props) => {
      grid.props = props;
      return <div data-testid="gateway-grid" />;
    },
  };
});
vi.mock("../EmailAlertDialog", () => ({
  default: (props) => {
    dialog.props = props;
    return props.open ? (
      <div>{props.alert ? `editing ${props.alert.name}` : "adding"}</div>
    ) : null;
  },
}));
vi.mock("../hooks/useEmailAlerts", () => ({
  useEmailAlerts: () => ({ data: state.alerts, isLoading: state.isLoading }),
  useUpdateEmailAlert: () => ({ mutate: state.update }),
  useDeleteEmailAlert: () => ({ mutate: state.remove }),
  useTestEmailAlert: () => ({ mutate: state.test, isPending: false }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) =>
    state.snackbars.push({ message, variant: options?.variant }),
}));

const ALERT = {
  id: "a1",
  name: "Errors to on-call",
  recipients: ["oncall@example.com", "lead@example.com", "cto@example.com"],
  events: ["error.occurred", "guardrail.triggered"],
  provider: "smtp",
  is_active: true,
  last_triggered_at: null,
  last_error: "SMTP 535 authentication failed",
  last_error_at: "2026-10-21T10:00:00Z",
};

const column = (name) =>
  grid.props.columnDefs.find((col) => col.headerName === name);

function renderActions(data = ALERT) {
  const Actions = column("").cellRenderer;
  render(<Actions data={data} context={grid.props.context} />);
}

// Email alerts on Gateway settings > General: the gateway sends an alert's
// events by email, so an alert can be added, switched on or off, edited,
// tested and deleted.
describe("Email alerts on General", () => {
  beforeEach(() => {
    state.alerts = [ALERT];
    state.isLoading = false;
    state.remove = vi.fn();
    state.update = vi.fn();
    state.test = vi.fn();
    state.snackbars = [];
    grid.props = null;
    dialog.props = null;
  });

  it("is an FA settings panel in sentence case, not a legacy MUI card and table", () => {
    render(<EmailAlertsCard />);
    screen.getByRole("region", { name: "Email alerts" });
    expect(screen.queryByText("Email Alerts")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("adds an alert from the panel", async () => {
    const user = userEvent.setup();
    render(<EmailAlertsCard />);
    const panel = screen.getByRole("region", { name: "Email alerts" });
    await user.click(within(panel).getByRole("button", { name: "Add alert" }));
    expect(screen.getByText("adding")).toBeInTheDocument();
  });

  it("offers Add alert with no alert saved yet", () => {
    state.alerts = [];
    render(<EmailAlertsCard />);
    const panel = screen.getByRole("region", { name: "Email alerts" });
    expect(
      within(panel).getByRole("button", { name: "Add alert" }),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText("No email alerts configured"),
    ).toBeInTheDocument();
  });

  it("edits a saved alert from its row", async () => {
    const user = userEvent.setup();
    render(<EmailAlertsCard />);
    renderActions();
    await user.click(
      screen.getByRole("button", { name: "Edit Errors to on-call" }),
    );
    expect(screen.getByText("editing Errors to on-call")).toBeInTheDocument();
  });

  it("switches an alert off from its row", async () => {
    const user = userEvent.setup();
    render(<EmailAlertsCard />);
    const Active = column("Active").cellRenderer;
    render(<Active data={ALERT} context={grid.props.context} />);
    const toggle = screen.getByRole("checkbox", {
      name: "Errors to on-call active",
    });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(state.update).toHaveBeenCalledWith({ id: "a1", is_active: false });
  });

  it("says when an alert was last sent and why its last delivery failed", () => {
    render(<EmailAlertsCard />);
    const Name = column("Name").cellRenderer;
    const { container } = render(<Name data={ALERT} />);
    expect(container).toHaveTextContent("Never sent");
    expect(container).toHaveTextContent(
      /Last delivery failed .*: SMTP 535 authentication failed/,
    );
  });

  it("sends a test email from the row and says how it went", async () => {
    const user = userEvent.setup();
    state.test = vi.fn((_vars, { onSuccess }) =>
      onSuccess({ status: true, result: { message: "Test email sent" } }),
    );
    render(<EmailAlertsCard />);
    renderActions();
    await user.click(
      screen.getByRole("button", {
        name: "Send a test email for Errors to on-call",
      }),
    );
    expect(state.test).toHaveBeenCalledWith({ id: "a1" }, expect.any(Object));
    expect(state.snackbars).toEqual([
      { message: "Test email sent", variant: "success" },
    ]);
  });

  it("deletes a saved alert from its row", async () => {
    const user = userEvent.setup();
    render(<EmailAlertsCard />);
    renderActions();
    await user.click(
      screen.getByRole("button", { name: "Delete Errors to on-call" }),
    );
    expect(state.remove).toHaveBeenCalledWith("a1");
  });

  it("carries no not-enforced note: the gateway sends these emails", () => {
    render(<EmailAlertsCard />);
    const panel = screen.getByRole("region", { name: "Email alerts" });
    expect(
      within(panel).queryByText(/Not enforced on this gateway/),
    ).toBeNull();
  });

  it("lists the alerts on the gateway grid, remembered per person", () => {
    render(<EmailAlertsCard />);
    expect(grid.props.stateKey).toBe("gateway-email-alerts");
    expect(grid.props.rowData).toEqual([ALERT]);
    expect(grid.props.columnDefs.map((col) => col.headerName)).toEqual([
      "Name",
      "Recipients",
      "Events",
      "Provider",
      "Active",
      "",
    ]);
  });

  it("gives every header room to be read whole, with a sort arrow", () => {
    render(<EmailAlertsCard />);
    const min = (name) => column(name).minWidth;
    expect(min("Events")).toBeGreaterThanOrEqual(124);
    expect(min("Provider")).toBeGreaterThanOrEqual(134);
    expect(min("Active")).toBeGreaterThanOrEqual(110);
  });
});
