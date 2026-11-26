import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const useRunAsks = vi.fn();
const useRunAsk = vi.fn();
vi.mock("src/api/simulate-environments/runAsks", async (orig) => ({
  ...(await orig()),
  useRunAsks: (...args) => useRunAsks(...args),
  useRunAsk: (...args) => useRunAsk(...args),
}));

const { default: RunAsksPanel } = await import("../RunAsksPanel");

// One question over four calls: two Yes, one No, one unmeasured.
const COUNTS = {
  requested: 4,
  answered: 3,
  unmeasured: 1,
  not_applicable: 0,
  grading_error: 0,
  infrastructure_error: 0,
  cancelled: 0,
  pending: 0,
  rate: { answered: 3, labels: { Yes: 2, No: 1 } },
};
const ASK = {
  id: "ask-1",
  status: "finished",
  stop_cause: "",
  created_at: "2026-11-26T10:00:00Z",
  asked_by: "Asha",
  selection_kind: "calls",
  note: "",
  requested: 4,
  credits_estimate: 1.5,
  questions: [
    {
      key: "q1",
      name: "Did the agent confirm the refund?",
      answer_type: "yes_no",
      labels: ["Yes", "No"],
      kinds: ["transcript"],
      counts: COUNTS,
    },
  ],
};
const row = (id, name, state, answer = null, cause = "") => ({
  call_execution_id: id,
  test_execution_id: "ex1",
  call_name: name,
  question_key: "q1",
  state,
  cause,
  answer,
  score: null,
  confidence: null,
  reason: answer ? `Because ${answer}` : null,
  finished_at: null,
});

const renderPanel = (props = {}) => {
  const onOpenAsk = vi.fn();
  const onOpenCall = vi.fn();
  const utils = render(
    <RunAsksPanel
      executionId="ex1"
      onOpenAsk={onOpenAsk}
      onOpenCall={onOpenCall}
      {...props}
    />,
  );
  return { ...utils, onOpenAsk, onOpenCall };
};

beforeEach(() => {
  useRunAsks.mockReset();
  useRunAsk.mockReset();
  useRunAsks.mockReturnValue({ data: { asks: [ASK] }, isLoading: false });
  useRunAsk.mockReturnValue({
    data: {
      ...ASK,
      results: [
        row("c1", "refund-angry · Trial 1", "answered", "Yes"),
        row("c2", "refund-angry · Trial 2", "unmeasured", null, "not_uploaded"),
      ],
    },
    isLoading: false,
  });
});

describe("RunAsksPanel", () => {
  it("shows requested and the six counts, and the answers out of those answered", () => {
    renderPanel();

    const question = screen.getByTestId("ask-question-q1");
    expect(within(question).getByTestId("ask-counts")).toHaveTextContent(
      "4 calls requested · Answered 3 · Unmeasured 1 · Not applicable 0 · Grading error 0 · Infrastructure error 0 · Cancelled 0",
    );
    expect(within(question).getByTestId("ask-rate")).toHaveTextContent(
      "Yes: 2 of 3 answered · No: 1 of 3 answered",
    );
    expect(useRunAsk).not.toHaveBeenCalled();
  });

  it("counts what is still pending while the ask runs", () => {
    useRunAsks.mockReturnValue({
      data: {
        asks: [
          {
            ...ASK,
            status: "running",
            questions: [
              {
                ...ASK.questions[0],
                counts: {
                  ...COUNTS,
                  answered: 1,
                  unmeasured: 0,
                  pending: 3,
                  rate: { answered: 1, labels: { Yes: 1 } },
                },
              },
            ],
          },
        ],
      },
      isLoading: false,
    });
    renderPanel();

    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByTestId("ask-counts")).toHaveTextContent("Pending 3");
  });

  it("lists each call with its answer, and the call's name opens that call", async () => {
    const user = userEvent.setup();
    const { onOpenCall } = renderPanel({ openAskId: "ask-1" });

    expect(useRunAsk).toHaveBeenCalledWith("ex1", "ask-1");
    const table = screen.getByRole("table", { name: "Answers per call" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByText("Yes")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Unmeasured")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Not uploaded")).toBeInTheDocument();

    await user.click(
      within(table).getByRole("button", { name: "refund-angry · Trial 2" }),
    );
    expect(onOpenCall).toHaveBeenCalledWith("c2");
  });

  it("opens and closes an ask from its card", async () => {
    const user = userEvent.setup();
    const { onOpenAsk } = renderPanel();

    await user.click(screen.getByRole("button", { name: "Show calls" }));
    expect(onOpenAsk).toHaveBeenCalledWith("ask-1");
  });

  it("says when nothing has been asked yet", () => {
    useRunAsks.mockReturnValue({ data: { asks: [] }, isLoading: false });
    renderPanel();

    expect(screen.getByText(/No questions asked yet/)).toBeInTheDocument();
  });

  it("offers asking about every call unless it is held back", () => {
    const onAsk = vi.fn();
    const { rerender } = renderPanel({ onAsk });
    expect(
      screen.getByRole("button", { name: "Ask about every call" }),
    ).toBeEnabled();

    rerender(
      <RunAsksPanel
        executionId="ex1"
        onOpenAsk={vi.fn()}
        onOpenCall={vi.fn()}
        onAsk={onAsk}
        askDisabledReason="Wait for this run to finish before asking about its calls"
      />,
    );
    expect(
      screen.getByRole("button", { name: "Ask about every call" }),
    ).toBeDisabled();
  });
});
