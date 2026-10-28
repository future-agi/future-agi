/* eslint-disable react/prop-types */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { renderHook } from "@testing-library/react";
import {
  renderWithRouter,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
import { palette } from "src/theme/palette";

const mocks = vi.hoisted(() => ({
  useCallDetail: vi.fn(),
  useCallExecutionV3Detail: vi.fn(),
  useCallTraces: vi.fn(),
  trackUrls: { current: null },
}));

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: (...a) => mocks.useCallDetail(...a),
    useCallExecutionV3Detail: (...a) => mocks.useCallExecutionV3Detail(...a),
    useCallTraces: (...a) => mocks.useCallTraces(...a),
  };
});

// The product voice drawer is stubbed to its seams: every tab the environment
// hands it is rendered, so a test reads exactly what the drawer would show.
vi.mock("src/components/VoiceDetailDrawerV2", () => ({
  default: ({
    leftPanelTabs = [],
    rightPanelTabs = [],
    rightPanelNotice,
    focusEvalName,
    hidePathTabs,
  }) => (
    <div
      data-testid="voice-drawer"
      data-focus-eval={focusEvalName ?? ""}
      data-hide-path-tabs={String(!!hidePathTabs)}
      data-left-tabs={leftPanelTabs.map((t) => t.label).join(",")}
      data-right-tabs={rightPanelTabs.map((t) => t.label).join(",")}
    >
      <div data-testid="voice-notice">{rightPanelNotice}</div>
      {[...leftPanelTabs, ...rightPanelTabs].map((t) => (
        <div key={t.value} data-testid={`voice-tab-${t.value}`}>
          {t.content}
        </div>
      ))}
    </div>
  ),
}));

vi.mock("../CallTracesPanel", () => ({
  default: ({ traces, isError }) => (
    <div
      data-testid="call-traces"
      data-count={traces.length}
      data-error={String(isError)}
    />
  ),
}));

vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));

vi.mock("src/hooks/use-stereo-channels", () => ({
  default: () => ({
    assistantUrl: "",
    customerUrl: "",
    loading: false,
    error: null,
  }),
}));

vi.mock(
  "src/components/multi-track-audio-player/MultiTrackAudioPlayer",
  () => ({
    default: (props) => {
      mocks.trackUrls.current = props.trackUrls;
      return <div data-testid="multi-track" />;
    },
    MemoizedBarsIcon: () => <span />,
  }),
);

const { default: CallDrawer } = await import("../CallDrawer");
const { mapCallDetail } = await import(
  "src/api/simulate-environments/runDetail"
);
const { StereoMultiTrackPlayer } = await import(
  "src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom"
);
const { useSpeakerColors } = await import(
  "src/components/VoiceDetailDrawerV2/speakerColors"
);

const RAW = {
  id: "call-1",
  call_type: "Outbound",
  duration: 42,
  provider: "vapi",
  transcript: [
    { speaker_role: "assistant", content: "Let me look that up." },
    { speaker_role: "user", content: "Thanks." },
  ],
  function_calls: [
    {
      name: "lookup_order",
      arguments: { order_id: "AB-1" },
      result: { status: "shipped" },
      duration_ms: 309,
      start_time_seconds: 3,
    },
    { name: "issue_refund", arguments: { amount: 20 } },
  ],
  sub_goal_results: [
    { name: "verify_identity", passed: true },
    { name: "issue_refund", passed: false },
    { name: "confirm_email", passed: null },
  ],
  eval_metrics: {
    e1: {
      name: "Refund correctness",
      value: "Failed",
      type: "Pass/Fail",
      reason: "wrong amount",
    },
    sg1: {
      name: "issue_refund",
      kind: "sub_goal",
      value: "Failed",
      type: "Pass/Fail",
      reason: "The refund was promised but never issued.",
    },
  },
  persona_details: {
    name: "Priya",
    age: "34",
    voice: "Calm",
    traits: ["impatient", "direct"],
  },
  ideal_outcome: "Refund issued in full",
};

const TASK = {
  id: "call-1",
  scenario: "Refund a damaged order",
  status: "failed",
  persona: "Priya",
  personaDetails: RAW.persona_details,
  idealOutcome: RAW.ideal_outcome,
  subGoalResults: RAW.sub_goal_results,
  harnessOutcomeStatus: "failed",
  evalResults: [
    {
      id: "e1",
      name: "Refund correctness",
      score: 0,
      passed: false,
      reason: "wrong amount",
    },
  ],
};

const chatTask = (extra = {}) => ({
  ...TASK,
  simulationCallType: "text",
  ...extra,
});
const voiceTask = (extra = {}) => ({
  ...TASK,
  simulationCallType: "voice",
  ...extra,
});

function given(raw = RAW) {
  mocks.useCallDetail.mockReturnValue({
    callDetail: mapCallDetail({ ...raw, simulation_call_type: "text" }),
    isLoading: false,
  });
  mocks.useCallExecutionV3Detail.mockReturnValue({
    data: { ...raw, simulation_call_type: "voice" },
    isPending: false,
    isError: false,
  });
}

function show(task, { route = "/", ...props } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <CallDrawer
        task={task}
        executionId="exec-1"
        agentType={task.simulationCallType}
        onClose={() => {}}
        {...props}
      />
    </QueryClientProvider>,
    { route },
  );
}

const voiceTab = (value) => screen.getByTestId(`voice-tab-${value}`);

afterEach(() => {
  // renderWithRouter pushes its route into the live URL; don't let
  // ?uiSample=graph leak into the next test.
  window.history.replaceState({}, "", "/");
  mocks.useCallTraces.mockReturnValue({
    projectId: null,
    traces: [],
    isError: false,
  });
});
mocks.useCallTraces.mockReturnValue({
  projectId: null,
  traces: [],
  isError: false,
});

describe("Call drawer v2", () => {
  it("Checklist tab lists sub-goals met of total from sub_goal_results; reference path is a soon chip", async () => {
    const user = userEvent.setup();
    given();
    show(chatTask());
    await user.click(screen.getByRole("tab", { name: "Checklist" }));
    expect(screen.getByText("1 of 3 sub-goals met")).toBeInTheDocument();
    expect(screen.getByText("verify_identity")).toBeInTheDocument();
    expect(screen.getByText("confirm_email")).toBeInTheDocument();
    // The sub-goal check's own reason, which the Evals tab leaves out.
    expect(
      screen.getByText("The refund was promised but never issued."),
    ).toBeInTheDocument();
    expect(screen.getByText("Reference path soon")).toBeInTheDocument();
    // Still not an eval.
    expect(screen.getByRole("tab", { name: "Evals (1)" })).toBeInTheDocument();

    show(voiceTask());
    const checklist = voiceTab("subGoals");
    expect(
      within(checklist).getByText("1 of 3 sub-goals met"),
    ).toBeInTheDocument();
    expect(
      within(checklist).getByText("Reference path soon"),
    ).toBeInTheDocument();
  });

  it("opening from an eval cell lands on that eval, expanded", async () => {
    given();
    show(chatTask(), { focusEvalId: "e1" });
    expect(screen.getByRole("tab", { name: "Evals (1)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // The reason shows in the failed-eval banner, and again on the row only
    // when that row is expanded.
    expect(screen.getAllByText("wrong amount")).toHaveLength(2);

    show(voiceTask(), { focusEvalId: "e1" });
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-focus-eval",
      "Refund correctness",
    );
  });

  it("Type reads Inbound or Outbound from call_type", () => {
    given();
    show(chatTask());
    expect(screen.getByText("Type :").nextSibling).toHaveTextContent(
      "Outbound",
    );

    given({ ...RAW, call_type: "Inbound" });
    show(chatTask());
    expect(screen.getAllByText("Type :")[1].nextSibling).toHaveTextContent(
      "Inbound",
    );
  });

  it("Attributes show persona name · age · voice, traits and expected outcome", async () => {
    const user = userEvent.setup();
    given();
    show(chatTask());
    await user.click(screen.getByRole("tab", { name: "Attributes" }));
    expect(screen.getByText("Priya · 34 · Calm")).toBeInTheDocument();
    expect(screen.getByText("impatient, direct")).toBeInTheDocument();
    expect(screen.getByText("Refund issued in full")).toBeInTheDocument();
    expect(screen.getByText("Must-pass soon")).toBeInTheDocument();

    show(voiceTask());
    const attributes = voiceTab("callAttributes");
    expect(
      within(attributes).getByText("Priya · 34 · Calm"),
    ).toBeInTheDocument();
    expect(
      within(attributes).getByText("impatient, direct"),
    ).toBeInTheDocument();
    expect(
      within(attributes).getByText("Refund issued in full"),
    ).toBeInTheDocument();
  });

  it("Graph tab says the call graph isn't in the API yet; ?uiSample=graph shows Vel's", async () => {
    const user = userEvent.setup();
    given();
    const plain = show(chatTask());
    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(
      screen.getByText("The call graph isn't in the API yet."),
    ).toBeInTheDocument();
    expect(screen.getByText("Call graph soon")).toBeInTheDocument();
    expect(screen.queryByLabelText("UI only")).not.toBeInTheDocument();
    plain.unmount();

    const voice = show(voiceTask());
    expect(
      within(voiceTab("callGraph")).getByText(
        "The call graph isn't in the API yet.",
      ),
    ).toBeInTheDocument();
    voice.unmount();

    show(chatTask(), { route: "/?uiSample=graph" });
    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(screen.getByLabelText("UI only")).toBeInTheDocument();
    expect(screen.getByTestId("call-graph")).toBeInTheDocument();
    expect(
      screen.queryByText("The call graph isn't in the API yet."),
    ).not.toBeInTheDocument();
  });

  it("Tool calls tab lists the real calls; read/write, rows and ms are soon chips", async () => {
    const user = userEvent.setup();
    given();
    show(chatTask());
    // The transcript already shows each call inline; the tab lists them again.
    const lookup = 'lookup_order({"order_id":"AB-1"})';
    expect(screen.getAllByText(lookup)).toHaveLength(1);
    await user.click(screen.getByRole("tab", { name: "Tool calls (2)" }));
    expect(screen.getAllByText(lookup)).toHaveLength(2);
    expect(screen.getAllByText('issue_refund({"amount":20})')).toHaveLength(2);
    expect(screen.getAllByText("Read/write soon")).toHaveLength(2);
    expect(screen.getAllByText("Rows soon")).toHaveLength(2);
    // Duration is sent per call when the harness timed it, so it's real: the
    // untimed call reads "-" rather than a soon chip.
    expect(screen.getByText("309ms")).toBeInTheDocument();
    expect(screen.queryByText("ms soon")).not.toBeInTheDocument();

    show(voiceTask());
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-right-tabs",
      "Tool calls (2),Call attributes",
    );
    expect(
      within(voiceTab("toolCalls")).getByText(
        'lookup_order({"order_id":"AB-1"})',
      ),
    ).toBeInTheDocument();
  });

  it("unmeasured calls show Vel's banner with the real outcome", () => {
    given();
    show(
      chatTask({ status: "unmeasured", harnessOutcomeStatus: "inconclusive" }),
    );
    expect(
      screen.getByText("Inconclusive. This scenario produced no verdict."),
    ).toBeInTheDocument();
    expect(screen.getByText("Cause soon")).toBeInTheDocument();

    show(voiceTask({ status: "error", harnessOutcomeStatus: "infra_error" }));
    const notice = screen.getByTestId("voice-notice");
    expect(
      within(notice).getByText("Error. This scenario produced no verdict."),
    ).toBeInTheDocument();
    expect(within(notice).getByText(/infra_error/)).toBeInTheDocument();
  });

  it("measured calls show no unmeasured banner", () => {
    given();
    show(chatTask());
    expect(screen.queryByText(/produced no verdict/)).not.toBeInTheDocument();
    show(voiceTask());
    expect(screen.getByTestId("voice-notice")).toBeEmptyDOMElement();
  });

  it("Traces still lists real traces", async () => {
    const user = userEvent.setup();
    given();
    mocks.useCallTraces.mockReturnValue({
      projectId: "p1",
      traces: [{ traceId: "t1", name: "book_ride" }],
      isError: false,
    });
    show(chatTask());
    await user.click(screen.getByRole("tab", { name: "Traces" }));
    expect(screen.getByTestId("call-traces")).toHaveAttribute(
      "data-count",
      "1",
    );

    show(voiceTask());
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-left-tabs",
      "Checklist,Graph,Traces",
    );
    // The product's own Checklist and Graph tabs stay off: they read a
    // scenario graph environment runs don't have.
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-hide-path-tabs",
      "true",
    );
    expect(
      within(voiceTab("traces")).getByTestId("call-traces"),
    ).toHaveAttribute("data-count", "1");
  });

  it("caller waveform uses the transcript's caller colour", () => {
    for (const mode of ["light", "dark"]) {
      const theme = createTheme({ palette: palette(mode) });
      const wrapper = ({ children }) => (
        <ThemeProvider theme={theme}>{children}</ThemeProvider>
      );
      const { result } = renderHook(() => useSpeakerColors(), { wrapper });
      renderWithRouter(
        <ThemeProvider theme={theme}>
          <StereoMultiTrackPlayer
            id={`call-${mode}`}
            recordings={{ customer: "c.wav", assistant: "a.wav" }}
          />
        </ThemeProvider>,
      );
      const caller = mocks.trackUrls.current.find(
        (t) => t.name === "Customer Audio",
      );
      expect(caller.color).toBe(result.current.user);
    }
  });
});
