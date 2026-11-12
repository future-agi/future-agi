import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestDetailDrawer from "../RequestDetailDrawer";
import RequestTable from "../RequestTable";

// The gateway records why a call it sent ended early (metadata.call_end).
// Logs names that cause in the row, the drawer and the timeline. A log
// written before the gateway recorded it keeps the neutral phrase. The
// charge sentence is unchanged.

const detail = vi.hoisted(() => ({
  state: { data: null, isLoading: false, error: null, refetch: () => {} },
}));
const logs = vi.hoisted(() => ({ rows: [] }));

vi.mock("../hooks/useRequestDetail", () => ({ default: () => detail.state }));
vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: logs.rows, count: logs.rows.length },
    isLoading: false,
    error: null,
    refetch: () => {},
  }),
}));
vi.mock("ag-grid-react", () => import("./fakeAgGrid"));
vi.mock("../../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));

afterEach(() => vi.restoreAllMocks());

const NEUTRAL = "Ended before the provider reported token counts";
const CHARGED =
  "The request was sent to the provider, which reported no token counts, so the reserved amount was charged.";
const ESTIMATED = "Usage was estimated: the provider reported no token counts.";

const PHRASES = {
  client_cancelled: "Cancelled by the client",
  provider_stopped: "Stopped by the provider",
  deadline: "Stopped at the request's deadline",
  gateway_timeout: "Stopped at the gateway's model timeout",
};

const ATTEMPT = {
  attempt_id: "01M74YH0111AAH49XKQWGW0PJA.1",
  index: 1,
  kind: "primary",
  provider: "openrouter",
  model: "liquid/lfm-3-1.7b",
  credential_type: "managed",
  status_code: 200,
  outcome: "error",
  error_class: "unknown",
  error_origin: "upstream",
  error_message: "reading provider response: unexpected EOF",
  started_at: "2026-11-11T09:20:20.004Z",
  latency_ms: 2980,
  billed_cost_micros: 3364,
  cost_source: "catalog_max",
  usage_estimated: true,
  billable: true,
};

// A managed call the gateway sent, that ended without usage and was charged
// at its hold, with the cause the gateway recorded (none on an older log).
const ended = (callEnd) => ({
  id: `log-${callEnd || "older"}`,
  request_id: "01M74YH0111AAH49XKQWGW0PJA",
  model: "liquid/lfm-3-1.7b",
  resolved_model: "liquid/lfm-3-1.7b",
  provider: "openrouter",
  status_code: 502,
  latency_ms: 2990,
  started_at: "2026-11-11T09:20:20.001Z",
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  cost: "0.003364",
  is_stream: false,
  is_error: true,
  error_message: "reading provider response: unexpected EOF",
  cache_hit: false,
  fallback_used: false,
  guardrail_triggered: false,
  api_key_id: "key_2e45",
  session_id: "",
  billing_mode: "managed",
  cost_source: "catalog_max",
  usage_estimated: true,
  attempt_count: 1,
  generation_id: "01M74YH0111AAH49XKQWGW0PJA",
  metadata: {
    billing_mode: "managed",
    cost_source: "catalog_max",
    usage_estimated: "true",
    usage_unreadable: "dispatched_without_usage",
    ...(callEnd ? { call_end: callEnd } : {}),
  },
  request_headers: {},
  response_headers: {},
  attempts: [ATTEMPT],
  billing: {
    mode: "managed",
    credential_type: "managed",
    billed: "0.003364",
    cost_source: "catalog_max",
    usage_estimated: true,
  },
  agent: {},
  routing: {},
});

// A managed stream its client stopped: charged on what was received (an
// estimate, not the hold), so no usage_unreadable mark.
const STOPPED_STREAM = {
  ...ended("client_cancelled"),
  id: "log-stopped-stream",
  status_code: 200,
  is_stream: true,
  is_error: false,
  error_message: "",
  cost: "0.000041",
  cost_source: "catalog",
  metadata: {
    billing_mode: "managed",
    cost_source: "catalog",
    usage_estimated: "true",
    call_end: "client_cancelled",
  },
  attempts: [
    {
      ...ATTEMPT,
      outcome: "success",
      error_class: "",
      error_origin: "",
      error_message: "",
      cost_source: "catalog",
    },
  ],
  billing: {
    mode: "managed",
    credential_type: "managed",
    billed: "0.000041",
    cost_source: "catalog",
    usage_estimated: true,
  },
};

const openDrawer = (log) => {
  detail.state = {
    data: { result: log },
    isLoading: false,
    error: null,
    refetch: () => {},
  };
  return render(<RequestDetailDrawer open logId={log.id} onClose={vi.fn()} />);
};

const note = () => screen.getByRole("region", { name: "How this call ended" });
const billing = () =>
  screen.getByRole("heading", { name: "Billing" }).closest("section");
const timelineRow = (kind) =>
  within(screen.getByRole("list", { name: "Request timeline" }))
    .getAllByRole("listitem")
    .find((row) => row.getAttribute("data-kind") === kind);

const renderRows = (rows) => {
  logs.rows = rows;
  render(
    <RequestTable
      filters={{}}
      setFilter={() => {}}
      setFilters={() => {}}
      onSelectLog={() => {}}
    />,
  );
  return within(screen.getByTestId("ag-grid")).getAllByRole("row").slice(1);
};

describe("Logs names why the gateway says a call ended", () => {
  for (const [value, phrase] of Object.entries(PHRASES)) {
    it(`reads ${value} as "${phrase}" in the drawer and the timeline`, () => {
      openDrawer(ended(value));
      expect(
        within(note()).getByRole("heading", { name: phrase }),
      ).toBeInTheDocument();
      expect(screen.queryByText(NEUTRAL)).toBeNull();
      expect(timelineRow("attempt")).toHaveTextContent(phrase);
      expect(billing()).toHaveTextContent(CHARGED);
    });
  }

  it("names each cause in its row", () => {
    const rows = renderRows(Object.keys(PHRASES).map((value) => ended(value)));
    Object.values(PHRASES).forEach((phrase, i) => {
      expect(
        within(rows[i]).getByRole("img", { name: phrase }),
      ).toBeInTheDocument();
    });
  });

  it("keeps the neutral phrase in the drawer for a log written before the gateway recorded the cause", () => {
    openDrawer(ended(null));
    expect(
      within(note()).getByRole("heading", { name: NEUTRAL }),
    ).toBeInTheDocument();
    expect(billing()).toHaveTextContent(CHARGED);
  });

  it("keeps the neutral phrase in the row for a log written before the gateway recorded the cause", () => {
    const [row] = renderRows([ended(null)]);
    expect(within(row).getByRole("img", { name: NEUTRAL })).toBeInTheDocument();
  });

  it("keeps the neutral phrase for a value it does not know", () => {
    openDrawer(ended("some_new_cause"));
    expect(
      within(note()).getByRole("heading", { name: NEUTRAL }),
    ).toBeInTheDocument();
  });

  it("names a stream its client stopped, without the charge sentence", () => {
    openDrawer(STOPPED_STREAM);
    expect(
      within(note()).getByRole("heading", { name: PHRASES.client_cancelled }),
    ).toBeInTheDocument();
    expect(timelineRow("response")).toHaveTextContent(
      `Responded 200${PHRASES.client_cancelled}`,
    );
    expect(billing()).not.toHaveTextContent(CHARGED);
    expect(billing()).toHaveTextContent(ESTIMATED);
  });
});
