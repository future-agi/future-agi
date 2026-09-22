import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const getHarnessJob = vi.fn();
const listHarnessJobs = vi.fn();
const sendHarnessConversationMessage = vi.fn();

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: (...args) => getHarnessJob(...args),
  listHarnessJobs: (...args) => listHarnessJobs(...args),
  cancelHarnessJob: vi.fn(),
  sendHarnessConversationMessage: (...args) =>
    sendHarnessConversationMessage(...args),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ jobId: "job-1" }),
  };
});

const { default: HarnessDetail } = await import("./HarnessDetail");

let eventSeq = 0;
const started = (stage, wall) => ({
  event_id: `e${(eventSeq += 1)}`,
  type: "harness.stage.started",
  payload: { stage },
  wall_time: wall,
});
const completed = (stage, wall) => ({
  event_id: `e${(eventSeq += 1)}`,
  type: "harness.stage.completed",
  payload: { stage },
  wall_time: wall,
});

const job = ({
  stage,
  state = "running",
  events = [],
  failure = null,
  cancelRequestedAt = null,
  conversation = null,
}) => ({
  job: {
    job_id: "job-1",
    run_id: "harness-job-1",
    scenario_count: 1,
    metadata: { agent_name: "ride-voice-e2e-4" },
  },
  status: {
    state,
    stage,
    failure,
    cancel_requested_at: cancelRequestedAt,
    completed_scenarios: 0,
    total_scenarios: 1,
    attempt: 1,
    updated_at: "2026-08-25T11:29:41Z",
  },
  events,
  credentials: { detected_connectors: ["http", "livekit"] },
  conversation,
});

const renderDetail = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <HarnessDetail />
      </QueryClientProvider>
    </HelmetProvider>,
  );
};

describe("HarnessDetail run checklist", () => {
  beforeEach(() => {
    getHarnessJob.mockReset();
    listHarnessJobs.mockReset();
    listHarnessJobs.mockResolvedValue([]);
    sendHarnessConversationMessage.mockReset();
  });

  it("folds finished stages away while a run is in flight", async () => {
    getHarnessJob.mockResolvedValue(
      job({
        stage: "running",
        events: [
          started("understand", "2026-08-25T11:13:56Z"),
          completed("understand", "2026-08-25T11:18:58Z"),
          started("calls", "2026-08-25T11:25:45Z"),
        ],
      }),
    );
    renderDetail();

    expect(await screen.findByText("10 stages complete")).toBeInTheDocument();
    // The stage list and the status chip both render the stage, now labelled
    // "Running scenarios" rather than the bare field name.
    expect(screen.getAllByText("Running scenarios").length).toBeGreaterThan(1);
    expect(screen.getByText("Grading results")).toBeInTheDocument();
    // Everything before the current stage is behind the summary row.
    expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  });

  it("shows requested, admitted, effective slots and actual queue counts", async () => {
    const value = job({ stage: "running" });
    value.parallelism = {
      requested: 10,
      admitted: 4,
      effective: 2,
      degrade_reasons: [],
    };
    value.status.active_scenarios = 1;
    value.status.queued_scenarios = 8;
    value.job.metadata.parallelism_clamped = { requested: 10, admitted: 4 };
    getHarnessJob.mockResolvedValue(value);
    renderDetail();
    expect(
      await screen.findByText(
        "World slots: 2 effective / 4 admitted / 10 requested · 1 active · 8 queued",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Requested 10, admitted 4/)).toBeInTheDocument();
  });

  it("reveals the whole list when the summary row is opened", async () => {
    getHarnessJob.mockResolvedValue(
      job({
        stage: "running",
        events: [started("understand", "2026-08-25T11:13:56Z")],
      }),
    );
    renderDetail();

    await userEvent.click(await screen.findByText("10 stages complete"));
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  // Collapsing folds away only the finished prefix, so the stage a run died on is always the
  // first row still on screen. That is the invariant, not "show everything".
  it("keeps a failed run's failing stage on screen", async () => {
    getHarnessJob.mockResolvedValue(
      job({
        stage: "failed",
        failure: { stage: "validating_environment", domain: "environment" },
        events: [started("environment", "2026-08-25T11:13:56Z")],
      }),
    );
    renderDetail();

    expect(await screen.findByText("Validating environment")).toBeInTheDocument();
    // Seven stages preceded it, so they fold away rather than padding the column.
    expect(screen.getByText("7 stages complete")).toBeInTheDocument();
    expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  });

  it("keeps a canceled run's stopped stage on screen", async () => {
    getHarnessJob.mockResolvedValue(
      job({
        stage: "canceled",
        events: [started("understand", "2026-08-25T11:13:56Z")],
      }),
    );
    renderDetail();

    expect(await screen.findByText("Understanding agent")).toBeInTheDocument();
    expect(screen.getByText("2 stages complete")).toBeInTheDocument();
  });

  it("keeps cancellation feedback visible while remote cleanup runs", async () => {
    getHarnessJob.mockResolvedValue(
      job({
        state: "cleaning_up",
        stage: "cleaning_up",
        cancelRequestedAt: "2026-08-29T06:10:26Z",
      }),
    );
    renderDetail();

    expect(await screen.findByText("Canceling…")).toBeDisabled();
    expect(
      screen.getByText(
        "Cancellation requested. The sandbox is stopping and cleaning up.",
      ),
    ).toBeInTheDocument();
  });

  it("renders the durable ALK conversation and sends a user message", async () => {
    const conversation = {
      conversation_id: "conversation-1",
      job_id: "job-1",
      state: "warm_idle",
      stage: "run",
      active_invocation_id: null,
      blocking_input: null,
      event_watermark: 2,
      runtime: {
        state: "active",
        warm_until: null,
        degraded: false,
        available: true,
      },
      events: [
        {
          event_id: "tool-started-1",
          sequence: 1,
          kind: "tool_started",
          stage: "understand",
          function_call_id: "call-1",
          payload: { tool: "request_adjustment" },
          emitted_at: "2026-09-16T00:00:30Z",
        },
        {
          event_id: "tool-result-1",
          sequence: 2,
          kind: "tool_result",
          stage: "understand",
          function_call_id: "call-1",
          payload: { tool: "request_adjustment", text: "pending" },
          emitted_at: "2026-09-16T00:00:31Z",
        },
      ],
      messages: [
        {
          message_id: "assistant-1",
          sequence: 1,
          role: "assistant",
          kind: "message",
          state: "completed",
          stage: "run",
          content: "The last run failed on scenario 12.",
          payload: {},
          invocation_id: "turn-1",
          function_call_id: null,
          reply_to: null,
          created_at: "2026-09-16T00:00:00Z",
        },
      ],
    };
    getHarnessJob.mockResolvedValue(
      job({ stage: "completed", state: "completed", conversation }),
    );
    sendHarnessConversationMessage.mockResolvedValue({
      ...conversation,
      state: "responding",
      messages: [
        ...conversation.messages,
        {
          message_id: "user-1",
          sequence: 2,
          role: "user",
          kind: "message",
          state: "queued",
          stage: "run",
          content: "Add five payment-failure scenarios",
          payload: {},
          invocation_id: null,
          function_call_id: null,
          reply_to: null,
          created_at: "2026-09-16T00:01:00Z",
        },
      ],
    });
    renderDetail();

    expect(
      await screen.findByText("The last run failed on scenario 12."),
    ).toBeInTheDocument();
    expect(screen.getByText("ALK used Request adjustment")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
    const input = screen.getByPlaceholderText(
      "Ask ALK about this environment or tell it what to change…",
    );
    await userEvent.type(input, "Add five payment-failure scenarios");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(sendHarnessConversationMessage).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        content: "Add five payment-failure scenarios",
        kind: "user_message",
      }),
    );
  });

  it("routes active-run questions through the conversation API", async () => {
    getHarnessJob.mockResolvedValue(
      job({ stage: "generating_scenarios", conversation: null }),
    );
    sendHarnessConversationMessage.mockResolvedValue({
      conversation_id: "conversation-active",
      job_id: "job-1",
      state: "starting",
      stage: "understand",
      active_invocation_id: null,
      blocking_input: null,
      event_watermark: 0,
      runtime: {
        state: "starting",
        warm_until: null,
        degraded: false,
        available: true,
      },
      events: [],
      messages: [],
    });
    renderDetail();

    const input = await screen.findByPlaceholderText(
      "Ask ALK about this environment or tell it what to change…",
    );
    await userEvent.type(input, "What is this agent about?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(sendHarnessConversationMessage).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        content: "What is this agent about?",
        kind: "user_message",
      }),
    );
  });

});
