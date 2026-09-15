import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const getHarnessJob = vi.fn();
const listHarnessJobs = vi.fn();

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: (...args) => getHarnessJob(...args),
  listHarnessJobs: (...args) => listHarnessJobs(...args),
  cancelHarnessJob: vi.fn(),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true }),
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
});

const renderDetail = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rendered = render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <HarnessDetail />
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return { ...rendered, client };
};

describe("HarnessDetail run checklist", () => {
  beforeEach(() => {
    getHarnessJob.mockReset();
    listHarnessJobs.mockReset();
    listHarnessJobs.mockResolvedValue([]);
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

    expect(
      await screen.findByText("Validating environment"),
    ).toBeInTheDocument();
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

  it("shows measured consumption and clears a recovered budget refusal", async () => {
    const paused = {
      ...job({ stage: "failed", state: "failed" }),
      consumption: null,
      usage_limit: {
        allowed: false,
        error_code: "BUDGET_PAUSED",
        dimension: "voice_sim_minutes",
        reason: "Voice simulation is paused by your budget",
        upgrade_cta: { text: "Review plan", plan: "payg" },
      },
    };
    getHarnessJob.mockResolvedValue(paused);
    const { client } = renderDetail();
    expect(
      await screen.findByText("Usage paused by budget"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Voice simulation is paused by your budget"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Review plan" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Usage unavailable")).toBeInTheDocument();

    const recovered = {
      ...paused,
      usage_limit: null,
      consumption: {
        text_sim_tokens: 1250,
        voice_sim_minutes: 0.25,
        ai_credits: 3,
        sandbox_seconds: 45,
      },
    };
    getHarnessJob.mockResolvedValue(recovered);
    await act(async () => {
      client.setQueryData(["harness-job", "job-1"], recovered);
    });
    await waitFor(() =>
      expect(
        screen.queryByText("Usage paused by budget"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("Usage unavailable")).not.toBeInTheDocument();
    expect(
      within(
        screen.getByText("Text simulation tokens").parentElement,
      ).getByText("1,250"),
    ).toBeInTheDocument();
    expect(
      within(
        screen.getByText("Voice simulation minutes").parentElement,
      ).getByText("0.25"),
    ).toBeInTheDocument();
  });
});
