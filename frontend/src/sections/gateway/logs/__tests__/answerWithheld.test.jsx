import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestTable from "../RequestTable";
import RequestDetailDrawer from "../RequestDetailDrawer";
import { statusOf } from "../logStatus";

// A guardrail refusal reads one of two ways. A request refused before the
// provider was called got no answer: "Blocked by a guardrail". An answer a
// guardrail on the answer withheld was given by the model, and its cost is
// recorded as for an answered request; the client got the refusal instead:
// "Answer withheld by a guardrail", and the drawer says so. The gateway marks
// the second in the row's metadata (answer_withheld "true"); the console never
// infers it from the cost.

vi.mock("ag-grid-react", () => import("./fakeAgGrid"));

const { state } = vi.hoisted(() => ({ state: { rows: [], detail: {} } }));

vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: state.rows, count: state.rows.length },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../hooks/useRequestDetail", () => ({
  default: () => ({
    data: { result: state.detail },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));

const SENTENCE =
  "The model answered, and a guardrail on the answer withheld it. The request's cost is recorded as for an answered request.";

const base = {
  model: "gpt-4o-mini",
  provider: "openai",
  status_code: 403,
  guardrail_triggered: true,
  is_error: true,
  error_message: "Request blocked by guardrail: answer-blocker",
  error_code: "content_blocked",
  latency_ms: 40,
  started_at: "2026-11-12T10:02:00Z",
  attempts: [],
};

const WITHHELD = {
  ...base,
  id: "withheld",
  request_id: "req-withheld",
  input_tokens: 5,
  output_tokens: 9,
  total_tokens: 14,
  cost: "0.000102",
  metadata: { guardrail_action: "blocked", answer_withheld: "true" },
};

const REFUSED_BEFORE = {
  ...base,
  id: "refused-before",
  request_id: "req-refused-before",
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  cost: "0.000000",
  metadata: { guardrail_action: "blocked" },
};

const tableProps = {
  filters: {},
  setFilter: vi.fn(),
  setFilters: vi.fn(),
  onSelectLog: vi.fn(),
  onClearFilters: vi.fn(),
};

const rows = () =>
  within(screen.getByTestId("ag-grid")).getAllByRole("row").slice(1);

describe("statusOf: a withheld answer", () => {
  it("is a guardrail block marked withheld only when the gateway marked it", () => {
    expect(statusOf(WITHHELD)).toMatchObject({ blocked: true, withheld: true });
    expect(statusOf(REFUSED_BEFORE)).toMatchObject({
      blocked: true,
      withheld: false,
    });
    // A mark on a row that is not a guardrail block does not make it one.
    expect(statusOf({ ...WITHHELD, guardrail_triggered: false })).toMatchObject(
      { blocked: false, withheld: false },
    );
    // A cost alone is never read as a withheld answer.
    expect(
      statusOf({ ...REFUSED_BEFORE, cost: "0.5", metadata: {} }),
    ).toMatchObject({ withheld: false });
  });
});

describe("the Logs table names how a guardrail refused", () => {
  it("flags a withheld answer and a refusal before the provider differently", () => {
    state.rows = [WITHHELD, REFUSED_BEFORE];
    render(<RequestTable {...tableProps} />);
    const [withheld, before] = rows();
    expect(
      within(withheld).getByLabelText("Answer withheld by a guardrail"),
    ).toBeInTheDocument();
    expect(
      within(withheld).queryByLabelText("Blocked by a guardrail"),
    ).not.toBeInTheDocument();
    expect(
      within(before).getByLabelText("Blocked by a guardrail"),
    ).toBeInTheDocument();
    expect(
      within(before).queryByLabelText("Answer withheld by a guardrail"),
    ).not.toBeInTheDocument();
  });
});

describe("the drawer explains a withheld answer", () => {
  it("says the model answered and how its cost is recorded", () => {
    state.detail = WITHHELD;
    render(<RequestDetailDrawer open logId="withheld" onClose={vi.fn()} />);
    expect(screen.getByText(SENTENCE)).toBeInTheDocument();
    expect(screen.queryByText(/charged/i)).toBeNull();
    // The gateway's message stays in the error box.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Request blocked by guardrail: answer-blocker",
    );
  });

  it("says nothing of the kind for a request refused before the provider", () => {
    state.detail = REFUSED_BEFORE;
    render(
      <RequestDetailDrawer open logId="refused-before" onClose={vi.fn()} />,
    );
    expect(screen.queryByText(SENTENCE)).not.toBeInTheDocument();
  });
});
