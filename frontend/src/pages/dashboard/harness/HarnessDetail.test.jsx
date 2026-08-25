import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
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

const job = ({ stage, events = [], failure = null }) => ({
  job: {
    job_id: "job-1",
    run_id: "harness-job-1",
    scenario_count: 1,
    metadata: { agent_name: "ride-voice-e2e-4" },
  },
  status: {
    stage,
    failure,
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
    // "Running" is also the status chip, so the stage row makes it the second occurrence.
    expect(screen.getAllByText("Running").length).toBeGreaterThan(1);
    expect(screen.getByText("Grading")).toBeInTheDocument();
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

    expect(await screen.findByText("Validating environment")).toBeInTheDocument();
    // Five stages preceded it, so they fold away rather than padding the column.
    expect(screen.getByText("5 stages complete")).toBeInTheDocument();
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
});
