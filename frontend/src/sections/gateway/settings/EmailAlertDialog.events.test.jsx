import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import EmailAlertDialog from "./EmailAlertDialog";
import EmailAlertsCard from "./EmailAlertsCard";

// Every event the dialog offers is produced by the backend
// (request events at ingestion, threshold events by the every-minute
// evaluator), the threshold events have the thresholds they are evaluated
// against, and the dialog says what the cooldown does.

const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();
const mockAlerts = vi.hoisted(() => ({ data: [] }));

vi.mock("./hooks/useEmailAlerts", () => ({
  useEmailAlerts: () => ({ data: mockAlerts.data, isLoading: false }),
  useCreateEmailAlert: () => ({ mutate: mockCreateMutate, isPending: false }),
  useUpdateEmailAlert: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useDeleteEmailAlert: () => ({ mutate: vi.fn(), isPending: false }),
  useTestEmailAlert: () => ({ mutate: vi.fn(), isPending: false }),
}));

const EVENT_LABELS = [
  "Budget Exceeded",
  "Error Occurred",
  "Error Rate Spike",
  "Guardrail Triggered",
  "Latency Spike",
  "Cost Threshold",
];

async function fillRequired(user) {
  await user.type(screen.getByLabelText(/Alert Name/), "Ops alert");
  const recipients = screen.getByLabelText(/Recipients/);
  await user.type(recipients, "ops@example.com{Enter}");
}

describe("EmailAlertDialog events and thresholds", () => {
  beforeEach(() => {
    mockCreateMutate.mockReset();
    mockUpdateMutate.mockReset();
    mockAlerts.data = [];
  });

  it("offers the six produced events and states when each is sent", () => {
    render(<EmailAlertDialog open onClose={vi.fn()} />);

    for (const label of EVENT_LABELS) {
      expect(screen.getByRole("checkbox", { name: label })).toBeInTheDocument();
    }
    // Both halves of the dedupe: a refusal that names its budget level is sent
    // once per level and period, one that does not name it once per error code
    // per UTC day.
    expect(
      screen.getByText(
        /once per budget level per budget period, or, when the gateway does not name the level, once per error code per UTC day\./i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/checked every minute/i)).toBeInTheDocument();
  });

  it("states the cooldown: one email per window, later events in the next email", () => {
    render(<EmailAlertDialog open onClose={vi.fn()} />);

    expect(
      screen.getByText(
        /at most one email per alert every cooldown period.*events in between are sent together in the next email/i,
      ),
    ).toBeInTheDocument();
  });

  it("shows threshold fields only for the threshold events and sends them", async () => {
    const user = userEvent.setup();
    render(<EmailAlertDialog open onClose={vi.fn()} />);

    expect(screen.queryByLabelText(/Error rate \(%\)/)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/p95 latency \(ms\)/),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Spend \(USD\)/)).not.toBeInTheDocument();

    await fillRequired(user);
    await user.click(
      screen.getByRole("checkbox", { name: "Error Rate Spike" }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Latency Spike" }));
    await user.click(screen.getByRole("checkbox", { name: "Cost Threshold" }));

    const rate = screen.getByLabelText(/Error rate \(%\)/);
    expect(rate).toHaveValue(5);
    expect(screen.getByLabelText(/p95 latency \(ms\)/)).toHaveValue(5000);
    expect(screen.getByLabelText(/Spend \(USD\)/)).toHaveValue(10);
    expect(screen.getByLabelText(/Window \(minutes\)/)).toHaveValue(5);
    expect(screen.getByLabelText(/Minimum requests/)).toHaveValue(5);

    await user.clear(rate);
    await user.type(rate, "12.5");
    const spend = screen.getByLabelText(/Spend \(USD\)/);
    await user.clear(spend);
    await user.type(spend, "250");

    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(mockCreateMutate).toHaveBeenCalledTimes(1);
    const payload = mockCreateMutate.mock.calls[0][0];
    expect(payload.events).toEqual([
      "error.rate_spike",
      "latency.spike",
      "cost.threshold",
    ]);
    expect(payload.thresholds).toEqual({
      error_rate_percent: 12.5,
      latency_ms: 5000,
      cost_usd: 250,
      window_minutes: 5,
      min_requests: 5,
    });
  });

  it("keeps stored thresholds when editing", async () => {
    const user = userEvent.setup();
    render(
      <EmailAlertDialog
        open
        onClose={vi.fn()}
        alert={{
          id: "alert-latency",
          name: "Latency",
          recipients: ["ops@example.com"],
          events: ["latency.spike"],
          thresholds: { latency_ms: 2500, window_minutes: 10, legacy_key: 1 },
          provider: "smtp",
          provider_config: { host: "smtp.example.com", port: 587 },
          cooldown_minutes: 15,
          is_active: true,
        }}
      />,
    );

    expect(screen.getByLabelText(/p95 latency \(ms\)/)).toHaveValue(2500);
    expect(screen.getByLabelText(/Window \(minutes\)/)).toHaveValue(10);

    await user.click(screen.getByRole("button", { name: "Update" }));

    const payload = mockUpdateMutate.mock.calls[0][0];
    expect(payload.thresholds).toEqual({
      legacy_key: 1,
      latency_ms: 2500,
      window_minutes: 10,
      min_requests: 5,
    });
  });

  it("drops a hidden stored threshold the backend would refuse", async () => {
    const user = userEvent.setup();
    render(
      <EmailAlertDialog
        open
        onClose={vi.fn()}
        alert={{
          id: "alert-errors",
          name: "Errors",
          recipients: ["ops@example.com"],
          events: ["error.occurred"],
          thresholds: { error_rate_percent: 0, cost_usd: 3, legacy_key: 2 },
          provider: "smtp",
          provider_config: { host: "smtp.example.com", port: 587 },
          cooldown_minutes: 5,
          is_active: true,
        }}
      />,
    );

    expect(screen.queryByLabelText(/Error rate \(%\)/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Update" }));

    expect(mockUpdateMutate).toHaveBeenCalledTimes(1);
    expect(mockUpdateMutate.mock.calls[0][0].thresholds).toEqual({
      cost_usd: 3,
      legacy_key: 2,
    });
  });

  it("refuses a threshold outside its range before saving", async () => {
    const user = userEvent.setup();
    render(<EmailAlertDialog open onClose={vi.fn()} />);
    await fillRequired(user);
    await user.click(
      screen.getByRole("checkbox", { name: "Error Rate Spike" }),
    );
    const rate = screen.getByLabelText(/Error rate \(%\)/);
    await user.clear(rate);
    await user.type(rate, "150");

    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(mockCreateMutate).not.toHaveBeenCalled();
    expect(
      screen.getByText(/error rate must be above 0 and at most 100/i),
    ).toBeInTheDocument();
  });
});

describe("EmailAlertsCard delivery status", () => {
  it("shows when each alert last sent and its last delivery failure", () => {
    mockAlerts.data = [
      {
        id: "a1",
        name: "Errors",
        recipients: ["ops@example.com"],
        events: ["error.occurred"],
        provider: "smtp",
        is_active: true,
        cooldown_minutes: 5,
        last_triggered_at: "2026-10-21T09:00:00Z",
        last_error: "",
        last_error_at: null,
      },
      {
        id: "a2",
        name: "Budget",
        recipients: ["ops@example.com"],
        events: ["budget.exceeded"],
        provider: "sendgrid",
        is_active: true,
        cooldown_minutes: 5,
        last_triggered_at: null,
        last_error: "SendGrid error (HTTP 401): bad key",
        last_error_at: "2026-10-21T09:05:00Z",
      },
    ];

    render(<EmailAlertsCard />);

    const rows = screen.getAllByRole("row");
    const errors = within(rows.find((r) => r.textContent.includes("Errors")));
    expect(errors.getByText(/^Last sent/)).toBeInTheDocument();
    const budget = within(rows.find((r) => r.textContent.includes("Budget")));
    expect(budget.getByText(/Never sent/)).toBeInTheDocument();
    expect(budget.getByText(/Last delivery failed/)).toBeInTheDocument();
    expect(budget.getByText(/HTTP 401/)).toBeInTheDocument();
  });
});
