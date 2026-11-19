import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { RECORDED_REPLAY_COPY as COPY } from "../recordedReplay.constants";

const withClient = (ui) => (
  <QueryClientProvider
    client={
      new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    }
  >
    {ui}
  </QueryClientProvider>
);

const mocks = vi.hoisted(() => ({
  replay: vi.fn(async () => ({
    run_test_id: "test-1",
    test_execution_id: "run-9",
  })),
  run: vi.fn(async () => ({
    run_test_id: "test-1",
    test_execution_id: "run-10",
  })),
  navigate: vi.fn(),
}));

vi.mock(
  "src/api/simulate-environments/harnessEnvironments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    replayTrialWithRecordedTools: (...args) => mocks.replay(...args),
    runHarnessEnvironment: (...args) => mocks.run(...args),
  }),
);

vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentVersions: () => ({
    data: {
      agent: [
        { id: "v1", label: "v1", isDefault: true, pickable: true },
        { id: "v2", label: "v2", isDefault: false, pickable: true },
        { id: "v3", label: "v3", isDefault: false, pickable: false },
      ],
    },
  }),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
}));

const { ReplayWithRecordedTools, RecordedReplayOrigin, ReplayStopped } =
  await import("../RecordedReplay");

const STOPPED = {
  stopped: { call: 3, tool: "refund_booking", diverged: true },
  live_run: { scenario_id: "scenario-a", agent_version_id: "v2" },
  agent_changed: true,
};

describe("A replay that stopped", () => {
  it("says where the changed agent took a different path, and runs it live", async () => {
    const user = userEvent.setup();
    render(
      withClient(
        <ReplayStopped
          envId="env-1"
          testId="test-1"
          recordedReplay={STOPPED}
        />,
      ),
    );

    expect(
      screen.getByText(
        "The changed agent took a different path at call 3: refund_booking",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: COPY.runLive }));

    await waitFor(() => expect(mocks.run).toHaveBeenCalledTimes(1));
    const [envId, scenarioIds, trials, key, picks] = mocks.run.mock.calls[0];
    expect([envId, scenarioIds, trials, picks]).toEqual([
      "env-1",
      ["scenario-a"],
      1,
      { agent: "v2" },
    ]);
    expect(key).toEqual(expect.any(String));
    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith(
        expect.stringContaining("/runs/test-1/run-10"),
      ),
    );
  });

  it("names the agent without 'changed' when the replay ran the same version", () => {
    render(
      withClient(
        <ReplayStopped
          envId="env-1"
          recordedReplay={{ ...STOPPED, agent_changed: false }}
        />,
      ),
    );

    expect(
      screen.getByText(
        "The agent took a different path at call 3: refund_booking",
      ),
    ).toBeInTheDocument();
  });

  it("offers a live run without a path sentence when the recording, not the agent, stopped it", () => {
    render(
      withClient(
        <ReplayStopped
          envId="env-1"
          recordedReplay={{
            ...STOPPED,
            stopped: { call: 1, tool: "get_order", diverged: false },
          }}
        />,
      ),
    );

    expect(screen.queryByText(/took a different path/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: COPY.runLive }),
    ).toBeInTheDocument();
  });

  it("shows nothing on a replay that did not stop", () => {
    const { container } = render(
      withClient(
        <ReplayStopped
          envId="env-1"
          recordedReplay={{ ...STOPPED, stopped: null }}
        />,
      ),
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe("Replay with recorded tools", () => {
  it("is off with the reason when the trial can't be replayed", async () => {
    const user = userEvent.setup();
    render(
      withClient(
        <ReplayWithRecordedTools
          envId="env-1"
          callId="call-1"
          recordedReplay={{
            available: false,
            unavailable_reason: "Recorded replay works for chat trials only.",
          }}
        />,
      ),
    );

    const button = screen.getByRole("button", { name: COPY.action });
    expect(button).toBeDisabled();
    await user.hover(button.parentElement);
    expect(
      await screen.findByText("Recorded replay works for chat trials only."),
    ).toBeInTheDocument();
  });

  it("replays the trial with the agent version picked, offering only versions a run can take", async () => {
    const user = userEvent.setup();
    render(
      withClient(
        <ReplayWithRecordedTools
          envId="env-1"
          testId="test-1"
          callId="call-1"
          recordedReplay={{
            available: true,
            unavailable_reason: null,
            original: null,
          }}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: COPY.action }));
    expect(screen.getByText(COPY.explain)).toBeInTheDocument();
    await user.click(screen.getByLabelText(COPY.agentLabel));
    expect(
      screen.queryByRole("option", { name: "v3" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "v2" }));
    await user.click(screen.getByRole("button", { name: COPY.submit }));

    await waitFor(() => expect(mocks.replay).toHaveBeenCalledTimes(1));
    const [envId, callId, key, agentVersionId] = mocks.replay.mock.calls[0];
    expect([envId, callId, agentVersionId]).toEqual(["env-1", "call-1", "v2"]);
    expect(key).toEqual(expect.any(String));
    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith(
        expect.stringContaining("/runs/test-1/run-9"),
      ),
    );
  });
});

describe("A replayed trial", () => {
  it("is labelled and says how the original trial ended, with the way to it", async () => {
    const user = userEvent.setup();
    render(
      <RecordedReplayOrigin
        envId="env-1"
        testId="test-1"
        original={{
          call_execution_id: "call-0",
          test_execution_id: "run-0",
          outcome: "failed",
        }}
      />,
    );

    expect(screen.getByTestId("recorded-replay-chip")).toHaveTextContent(
      COPY.label,
    );
    expect(screen.getByText(COPY.original("Failed"))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: COPY.openOriginal }));
    expect(mocks.navigate).toHaveBeenCalledWith(
      expect.stringContaining("/runs/test-1/run-0?rowId=call-0"),
    );
  });
});
