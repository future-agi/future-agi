import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestDetailDrawer from "../RequestDetailDrawer";
import RequestTable from "../RequestTable";

// A call that ended before its answer finished reads one vocabulary in the
// Logs row, the request drawer and its timeline, read from the fields the
// log API returns: the request's usage_unreadable mark
// (dispatched_without_usage: the gateway sent the call and it ended without
// usage) and the latest attempt's outcome. The error text is shown, never
// read: the same text without the mark renders as any other error.

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

const ENDED = "Ended before the provider reported token counts";
const CANCELLED = "Cancelled by the client";
const CHARGED =
  "The request was sent to the provider, which reported no token counts, so the reserved amount was charged.";
const ESTIMATED = "Usage was estimated: the provider reported no token counts.";
const ERROR_TEXT = "reading provider response: context canceled";

// The walked call: one non-streamed chat call on Future AGI credits that its
// client aborted after 3 s. The gateway logged a 502, its one attempt as the
// provider's 200 that ended in an unclassified error, and charged it once at
// its hold (price source catalog_max, usage estimated).
const ATTEMPT = {
  attempt_id: "01M74YH0111AAH49XKQWGW0PJA.1",
  index: 1,
  kind: "primary",
  reason: "",
  provider: "openrouter",
  model: "liquid/lfm-3-1.7b",
  credential_type: "managed",
  status_code: 200,
  outcome: "error",
  error_class: "unknown",
  error_origin: "upstream",
  error_message: ERROR_TEXT,
  started_at: "2026-11-11T09:20:20.004Z",
  latency_ms: 2980,
  prompt_tokens: 0,
  completion_tokens: 0,
  billed_cost_micros: 3364,
  cost_source: "catalog_max",
  usage_estimated: true,
  billable: true,
};

const WALKED = {
  id: "log-cancel",
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
  error_message: ERROR_TEXT,
  cache_hit: false,
  fallback_used: false,
  guardrail_triggered: false,
  api_key_id: "key_2e45",
  user_id: "walker@example.com",
  session_id: "",
  billing_mode: "managed",
  cost_source: "catalog_max",
  usage_estimated: true,
  attempt_count: 1,
  metadata: {
    billing_mode: "managed",
    cost_source: "catalog_max",
    usage_estimated: "true",
    usage_unreadable: "dispatched_without_usage",
    managed_credits_remaining: "4.994532",
    auth_key_name: "cancel-walk",
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
};

const withoutMark = (log) => {
  const { usage_unreadable: _mark, ...metadata } = log.metadata;
  return { ...log, metadata };
};

// A managed stream the gateway cut before the provider's usage chunk: the
// client had its 200, the attempt started fine, and the call was charged at
// its hold.
const CUT_STREAM = {
  ...WALKED,
  id: "log-cut-stream",
  status_code: 200,
  is_stream: true,
  is_error: false,
  error_message: "",
  input_tokens: 12,
  output_tokens: 40,
  attempts: [
    {
      ...ATTEMPT,
      outcome: "success",
      error_class: "",
      error_origin: "",
      error_message: "",
    },
  ],
};

// An attempt the gateway classified as cancelled by its caller.
const CLIENT_CANCELLED = {
  ...WALKED,
  id: "log-client-cancel",
  error_message: "context canceled",
  attempts: [
    {
      ...ATTEMPT,
      status_code: 0,
      outcome: "cancelled",
      error_class: "cancelled",
      error_origin: "client",
      error_message: "context canceled",
    },
  ],
};

// Cancelled before the call was sent: no mark, nothing charged.
const CANCELLED_UNSENT = {
  ...withoutMark(CLIENT_CANCELLED),
  id: "log-cancel-unsent",
  cost: "0",
  cost_source: "catalog",
  usage_estimated: false,
  metadata: {
    ...withoutMark(CLIENT_CANCELLED).metadata,
    cost_source: "catalog",
    usage_estimated: "false",
  },
  attempts: [
    {
      ...CLIENT_CANCELLED.attempts[0],
      billable: false,
      billed_cost_micros: 0,
      usage_estimated: false,
    },
  ],
  billing: {
    ...WALKED.billing,
    billed: "0",
    cost_source: "catalog",
    usage_estimated: false,
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

const sectionTitled = (title) =>
  screen.getByRole("heading", { name: title }).closest("section");

const timelineRow = (kind) =>
  within(screen.getByRole("list", { name: "Request timeline" }))
    .getAllByRole("listitem")
    .find((row) => row.getAttribute("data-kind") === kind);

describe("A call that ended before the provider reported usage", () => {
  it("reads one calm note, not an error banner, and keeps the error text", () => {
    openDrawer(WALKED);
    expect(screen.queryByRole("alert")).toBeNull();
    const note = screen.getByRole("region", { name: "How this call ended" });
    expect(
      within(note).getByRole("heading", { name: ENDED }),
    ).toBeInTheDocument();
    expect(note).toHaveTextContent(
      "The request was sent to the provider and ended before the answer finished. The log does not say whether the client, the provider or the request's deadline ended it.",
    );
    expect(note).toHaveTextContent(`Error text: ${ERROR_TEXT}`);
  });

  it("says in Billing why the reserved amount was charged", () => {
    openDrawer(WALKED);
    const billing = sectionTitled("Billing");
    expect(billing).toHaveTextContent(CHARGED);
    expect(billing).not.toHaveTextContent(ESTIMATED);
    expect(within(billing).getByText("Billed").parentElement).toHaveTextContent(
      "$0.003364",
    );
  });

  it("names how the call ended in the timeline instead of 200 · Unknown", () => {
    openDrawer(WALKED);
    const attempt = timelineRow("attempt");
    expect(attempt).toHaveTextContent(`${ENDED} · 2.98 s · Billed`);
    expect(attempt).not.toHaveTextContent("Unknown");
    expect(attempt).not.toHaveTextContent("200 ·");
    expect(timelineRow("response")).toHaveTextContent("Responded 502");
  });

  it("keeps the error banner and the attempt's status for the same error text without the gateway's mark", () => {
    openDrawer(withoutMark(WALKED));
    expect(screen.getByRole("alert")).toHaveTextContent(ERROR_TEXT);
    expect(
      screen.queryByRole("region", { name: "How this call ended" }),
    ).toBeNull();
    expect(screen.queryByText(ENDED)).toBeNull();
    expect(timelineRow("attempt")).toHaveTextContent("200 · Unknown");
    expect(sectionTitled("Billing")).toHaveTextContent(ESTIMATED);
    expect(sectionTitled("Billing")).not.toHaveTextContent(CHARGED);
  });

  it("puts the same words under Responded 200 for a stream cut before its usage", () => {
    openDrawer(CUT_STREAM);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      within(
        screen.getByRole("region", { name: "How this call ended" }),
      ).getByRole("heading", { name: ENDED }),
    ).toBeInTheDocument();
    expect(timelineRow("response")).toHaveTextContent(`Responded 200${ENDED}`);
    expect(timelineRow("attempt")).toHaveTextContent(ENDED);
    expect(sectionTitled("Billing")).toHaveTextContent(CHARGED);
  });
});

describe("A call its client cancelled", () => {
  it("reads Cancelled by the client in the note and the timeline, with the charge sentence", () => {
    openDrawer(CLIENT_CANCELLED);
    expect(screen.queryByRole("alert")).toBeNull();
    const note = screen.getByRole("region", { name: "How this call ended" });
    expect(
      within(note).getByRole("heading", { name: CANCELLED }),
    ).toBeInTheDocument();
    expect(note).toHaveTextContent(
      "The client closed the request before the answer finished.",
    );
    expect(note).toHaveTextContent("Error text: context canceled");
    expect(timelineRow("attempt")).toHaveTextContent(CANCELLED);
    expect(sectionTitled("Billing")).toHaveTextContent(CHARGED);
  });

  it("has no charge sentence when the call was cancelled before it was sent", () => {
    openDrawer(CANCELLED_UNSENT);
    const note = screen.getByRole("region", { name: "How this call ended" });
    expect(
      within(note).getByRole("heading", { name: CANCELLED }),
    ).toBeInTheDocument();
    expect(sectionTitled("Billing")).not.toHaveTextContent(CHARGED);
  });
});

describe("The Logs row of a call that ended before the provider reported usage", () => {
  it("carries the phrase beside its status; the same error without the mark does not", () => {
    logs.rows = [WALKED, { ...withoutMark(WALKED), id: "plain-502" }];
    render(
      <RequestTable
        filters={{}}
        setFilter={() => {}}
        setFilters={() => {}}
        onSelectLog={() => {}}
      />,
    );
    const [marked, plain] = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    expect(
      within(marked).getByRole("img", { name: ENDED }),
    ).toBeInTheDocument();
    expect(within(marked).getByText("502")).toBeInTheDocument();
    expect(within(plain).queryByRole("img", { name: ENDED })).toBeNull();
    expect(within(plain).getByText("502")).toBeInTheDocument();
  });
});
