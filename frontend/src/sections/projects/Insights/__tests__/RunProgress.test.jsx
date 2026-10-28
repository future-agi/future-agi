import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import RunProgress from "../RunProgress";
import { insightsKeys, useInsightsRun } from "../insightsApi";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  read: vi.fn(),
  cloud: false,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: mocks.cloud }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => mocks.post(...args) },
  readQuery: (...args) => mocks.read(...args),
  endpoints: {
    project: {
      insightsRun: (id) => `/tracer/insights/runs/${id}/`,
      insightsRunStop: (id) => `/tracer/insights/runs/${id}/stop/`,
    },
  },
}));

const NOW = new Date("2026-10-28T08:00:00Z").getTime();
const ago = (minutes) => new Date(NOW - minutes * 60_000).toISOString();

const running = (overrides = {}) => ({
  id: "run-1",
  status: "running",
  stage: "summarising",
  unit: "sessions",
  sample_size: 49,
  created_at: ago(20),
  started_at: ago(20),
  ...overrides,
  progress: {
    stage: "summarising",
    done: 1,
    total: 49,
    ...overrides.progress,
  },
});

function renderProgress(run) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(insightsKeys.run(run.id), run);
  render(
    <QueryClientProvider client={client}>
      <RunProgress run={run} />
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  mocks.cloud = false;
});

describe("RunProgress", () => {
  it("gives the usual time while a run is young", () => {
    renderProgress(
      running({ started_at: ago(3), progress: { updated_at: ago(1) } }),
    );
    expect(screen.getByText(/^Usually 2–10 minutes\./)).toBeInTheDocument();
  });

  it("says a run has taken longer than usual, without calling it stuck", () => {
    renderProgress(running({ progress: { updated_at: ago(2) } }));
    expect(screen.getByText(/^Taking longer than usual\./)).toBeInTheDocument();
    expect(screen.queryByText(/Usually 2–10 minutes/)).toBeNull();
  });

  it("says nothing has moved, and that it can be stopped, when no item finished for a long time", () => {
    renderProgress(running({ progress: { updated_at: ago(17) } }));
    expect(
      screen.getByText(
        "No progress in 17 minutes: the analysis model may be slow or not answering. The report keeps trying until it has run for 2 hours, or you can stop it.",
      ),
    ).toBeInTheDocument();
  });

  it("never calls a run stuck when its progress carries no time", () => {
    renderProgress(running());
    expect(screen.getByText(/^Taking longer than usual\./)).toBeInTheDocument();
  });

  it("stops a run only after confirming, and shows it stopped at once", async () => {
    const stopped = {
      id: "run-1",
      status: "failed",
      error: "Stopped by Ada.",
      error_code: "stopped",
    };
    mocks.post.mockResolvedValue({ data: { status: true, result: stopped } });
    const client = renderProgress(running());

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Stop this run?")).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(mocks.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Stop run",
      }),
    );
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post.mock.calls[0][0]).toBe(
      "/tracer/insights/runs/run-1/stop/",
    );
    await waitFor(() =>
      expect(client.getQueryData(insightsKeys.run("run-1"))).toMatchObject(
        stopped,
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes once the stopped run is read again, so the page goes straight to its counts", async () => {
    const stopped = {
      id: "run-1",
      status: "failed",
      error: "Stopped by Ada.",
      error_code: "stopped",
    };
    // Read again after the stop: what had been read before it, final.
    const reread = {
      ...running({ progress: { done: 8 } }),
      ...stopped,
      analysed_count: 8,
      finished_at: new Date(NOW).toISOString(),
    };
    let answer;
    mocks.read
      .mockResolvedValueOnce({ data: { status: true, result: running() } })
      .mockImplementation(
        () =>
          new Promise((resolve) => {
            answer = () => resolve({ data: { status: true, result: reread } });
          }),
      );
    mocks.post.mockResolvedValue({ data: { status: true, result: stopped } });
    // The report page: the run's progress while it runs, else how it ended.
    function Page() {
      const { data } = useInsightsRun("run-1");
      if (!data) return null;
      if (data.status === "running") return <RunProgress run={data} />;
      return <p>{`Stopped · ${data.analysed_count ?? 0} analysed`}</p>;
    }
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <Page />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));
    await waitFor(() => expect(mocks.read).toHaveBeenCalledTimes(2));
    expect(mocks.post).toHaveBeenCalledTimes(1);
    // Stopped, but not read again yet: still the dialog, no counts.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText(/^Stopped · /)).toBeNull();

    answer();
    expect(await screen.findByText("Stopped · 8 analysed")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText("Stopped · 0 analysed")).toBeNull();
  });

  it.each([
    [
      true,
      "It stops reading now and won't write a report. You'll be charged for the work finished before you stop. You can run it again later.",
    ],
    [
      false,
      "It stops reading now and won't write a report. You can run it again later.",
    ],
  ])(
    "says before stopping that the work finished before it is charged (Cloud: %s)",
    async (cloud, says) => {
      mocks.cloud = cloud;
      renderProgress(running());
      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText(says)).toBeInTheDocument();
    },
  );

  it("keeps the dialog open with the reason when the run can't be stopped", async () => {
    mocks.post.mockRejectedValue({
      statusCode: 400,
      result: "This run has already finished.",
    });
    renderProgress(running());
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));
    expect(
      await within(dialog).findByText("This run has already finished."),
    ).toBeInTheDocument();
  });
});
