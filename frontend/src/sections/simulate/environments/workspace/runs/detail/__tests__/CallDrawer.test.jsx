import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The chat drawer's data hook is mocked so the render asserts the drawer wiring
// against a fixed CallDetail rather than the network. `isVoiceCall` (the routing
// decision under test) stays real — it lives in its own `callRouting` module.
const useCallDetail = vi.fn();
const useCallExecutionV3Detail = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: (...a) => useCallDetail(...a),
    useCallExecutionV3Detail: (...a) => useCallExecutionV3Detail(...a),
  };
});

// The voice branch reuses the real product drawer; stub it to a marker so the
// test proves the routing without mounting the heavy component (imagine store,
// saved views, share dialog).
vi.mock("src/components/VoiceDetailDrawerV2", () => ({
  default: ({ data }) => (
    <div data-testid="voice-drawer">
      voice:{data?.id}:{data?.transcript?.map((turn) => turn.content).join("|")}
    </div>
  ),
}));

const { default: CallDrawer } = await import("../CallDrawer");
const { mapCallDetail } = await import("src/api/simulate-environments/runDetail");
const { mapCallRow } = await import("src/api/simulate-environments/runCalls");
const { isVoiceCall } = await import("../callRouting");

// Built through the REAL `mapCallDetail`, from a raw `call-executions/{id}/`
// body shaped the way the backend actually sends it, rather than
// hand-writing the CallDetail view-model literal — a hand-written literal
// exercises nothing about the mapper itself, so this closes that gap.
const RAW_CHAT_PAYLOAD = {
  id: "chat-1",
  simulation_call_type: "text",
  provider: "openai",
  duration: 30,
  transcript: [
    {
      speaker_role: "agent",
      content: "Refund issued.",
      tool_calls: [{ function: { name: "issue_refund" } }],
    },
    { speaker_role: "customer", content: "thanks" },
  ],
  turn_count: 2,
  avg_agent_latency: 800,
  agent_talk_percentage: 50,
  total_tokens: 1200,
  call_summary: "ok",
  eval_metrics: {
    e1: {
      name: "Refund correctness",
      value: "Failed",
      type: "Pass/Fail",
      reason: "wrong amount",
      removed: false,
    },
    // Removed AND failing: this is the verdict that must surface with a
    // "Removed" marker wherever the call's verdicts render — the banner
    // included. A removed-but-passing verdict wouldn't hit the banner at
    // all, so it can't stand in for that case.
    e2: {
      name: "no_misselling",
      value: "Failed",
      type: "Pass/Fail",
      reason: "flagged upsell",
      removed: true,
    },
  },
};

const CHAT_DETAIL = mapCallDetail(RAW_CHAT_PAYLOAD);

// `chatTask.evalResults` is the list-derived fallback slot
// (`ChatCallDrawer.jsx`'s `callDetail?.evalResults ?? task.evalResults ?? []`),
// which in production is filled by `mapCallRow` (`runCalls.js`) — a
// different mapper than `mapCallDetail` above. Routing the SAME raw
// `eval_metrics` through the real `mapCallRow` exercises that mapper too,
// rather than just reusing `mapCallDetail`'s output.
const LIST_EVAL_COLUMNS = [
  { id: "e1", type: "evaluation" },
  { id: "e2", type: "evaluation" },
];

const chatTask = {
  id: "chat-1",
  scenario: "Refund a double charge",
  persona: "Impatient caller",
  status: "failed",
  simulationCallType: "text",
  turns: 2,
  provider: "openai",
  evalResults: mapCallRow(RAW_CHAT_PAYLOAD, LIST_EVAL_COLUMNS).evalResults,
};

describe("isVoiceCall", () => {
  it("routes on the per-call type first, then the run-level agentType", () => {
    expect(isVoiceCall({ simulationCallType: "text" }, "voice")).toBe(false);
    expect(isVoiceCall({ simulationCallType: "voice" }, "text")).toBe(true);
    expect(isVoiceCall({ simulationCallType: null }, "voice")).toBe(true);
    expect(isVoiceCall({ simulationCallType: null }, "text")).toBe(false);
  });
});

describe("CallDrawer — chat branch", () => {
  it("renders the ported drawer with transcript, analytics and the failed-eval banner", async () => {
    useCallDetail.mockReturnValue({
      callDetail: CHAT_DETAIL,
      isLoading: false,
    });
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);

    // Header paints from the row.
    expect(screen.getByText(/Conversation ID/)).toBeInTheDocument();
    // Transcript (left pane, default) — the agent turn and its inline tool call.
    expect(screen.getByText("Refund issued.")).toBeInTheDocument();
    expect(screen.getByText(/issue_refund/)).toBeInTheDocument();
    // Analytics (right pane, default) — real tokens from the detail.
    expect(screen.getByText("1,200")).toBeInTheDocument();
    // The failed eval surfaces as a banner without switching tabs.
    expect(screen.getByText(/Refund correctness failed/)).toBeInTheDocument();
  });

  it("shows the per-eval score + reason on the Evals tab", async () => {
    useCallDetail.mockReturnValue({
      callDetail: CHAT_DETAIL,
      isLoading: false,
    });
    const user = userEvent.setup();
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);

    // The reason shows once in the failed-eval banner already.
    expect(screen.getAllByText("wrong amount")).toHaveLength(1);
    await user.click(screen.getByRole("tab", { name: /Evals \(2\)/ }));
    // The Evals tab adds its own row — banner + tab = two occurrences.
    expect(screen.getAllByText("wrong amount")).toHaveLength(2);
  });

  it("does not mount the product voice drawer for a chat call", () => {
    useCallDetail.mockReturnValue({
      callDetail: CHAT_DETAIL,
      isLoading: false,
    });
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);
    expect(screen.queryByTestId("voice-drawer")).toBeNull();
  });

  it("still lists the verdict of an eval that was removed, marked, everywhere call details render it", async () => {
    useCallDetail.mockReturnValue({ callDetail: CHAT_DETAIL, isLoading: false });
    const user = userEvent.setup();
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);

    // The removed eval also failed, so it surfaces in the banner too —
    // marked there, same as the Evals tab row.
    expect(screen.getByText(/no_misselling failed/)).toBeInTheDocument();
    expect(screen.getAllByText("Removed")).toHaveLength(1);
    // An e2e test locates the marker by id — assert it renders alongside
    // the text, not just the text.
    expect(screen.getAllByTestId("removed-eval-marker")).toHaveLength(1);

    await user.click(screen.getByRole("tab", { name: /Evals \(2\)/ }));

    expect(screen.getByText("no_misselling")).toBeInTheDocument();
    // Marked in both the banner and the Evals tab row now.
    expect(screen.getAllByText("Removed")).toHaveLength(2);
    expect(screen.getAllByTestId("removed-eval-marker")).toHaveLength(2);
    // The live failing eval never gets a marker in either place — the total
    // stays at exactly 2 (one per surface, both for the removed eval only).
    expect(screen.getByText(/Refund correctness failed/)).toBeInTheDocument();
  });

  it("marks a removed verdict on the list-derived fallback too, while the call detail hasn't loaded (or never does)", async () => {
    // `useCallDetail` returns no detail yet — the drawer falls back to
    // `task.evalResults`, which is what `runCalls.js`'s `mapCallRow` built
    // from the run's call-list rows. If that mapper ever drops `removed`
    // again, this is the test that catches it: `chatTask.evalResults` is
    // built through the REAL `mapCallRow` from the same raw `eval_metrics`
    // `CHAT_DETAIL` uses, so both paths are genuinely exercised, not just
    // asserted to agree.
    useCallDetail.mockReturnValue({ callDetail: null, isLoading: false });
    const user = userEvent.setup();
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);

    expect(screen.getByText(/no_misselling failed/)).toBeInTheDocument();
    expect(screen.getAllByText("Removed")).toHaveLength(1);
    expect(screen.getAllByTestId("removed-eval-marker")).toHaveLength(1);

    await user.click(screen.getByRole("tab", { name: /Evals \(2\)/ }));
    expect(screen.getAllByText("Removed")).toHaveLength(2);
    expect(screen.getAllByTestId("removed-eval-marker")).toHaveLength(2);
  });
});

// The chat drawer's verdict list is `callDetail?.evalResults ?? task.evalResults`,
// and `task.evalResults` is built by `mapCallRow` from the run-detail list
// endpoint's own payload. So whether a removed eval's stored verdict can be
// seen while the call-details request is still in flight depends on that
// endpoint keeping the verdict AND its `column_order` entry — the backend
// change this fixture is shaped after. Everything below the fixture is the
// production path: the same column filter `useRunCalls` applies, the real
// `mapCallRow`, the real drawer.
describe("CallDrawer — the run-detail endpoint's removed verdicts, through to the drawer", () => {
  // One page of `GET /simulate/test-executions/{id}/`, exactly as the widened
  // endpoint sends it: the removed evaluation keeps its column in
  // `column_order`, and its stored verdict rides along on the row marked
  // `removed: true`.
  const RUN_DETAIL_PAGE = {
    count: 1,
    column_order: [
      { id: "call_details", type: "call_details", column_name: "Call Details" },
      {
        id: "eval-live",
        type: "evaluation",
        column_name: "Refund correctness",
        eval_config: { output: "Pass/Fail" },
      },
      {
        id: "eval-removed",
        type: "evaluation",
        column_name: "no_misselling",
        eval_config: { output: "Pass/Fail" },
      },
    ],
    results: [
      {
        id: "chat-9",
        status: "completed",
        simulation_call_type: "text",
        provider: "openai",
        turn_count: 2,
        scenario: "Refund a double charge",
        customer_name: "Impatient caller",
        eval_metrics: {
          "eval-live": {
            name: "Refund correctness",
            value: "Failed",
            type: "Pass/Fail",
            reason: "wrong amount",
            removed: false,
          },
          "eval-removed": {
            name: "no_misselling",
            value: "Failed",
            type: "Pass/Fail",
            reason: "flagged upsell",
            removed: true,
          },
        },
      },
    ],
  };

  // The same derivation `useRunCalls` performs on that payload.
  const taskFromPage = (page) => {
    const evalColumns = page.column_order.filter((c) => c.type === "evaluation");
    return mapCallRow(page.results[0], evalColumns);
  };

  it("shows a removed eval's stored verdict, marked, from the run-detail payload alone while the call-details request is still pending", async () => {
    // Pending, not failed: this is the ordinary first paint of the drawer,
    // where the only verdicts it has are the list's.
    useCallDetail.mockReturnValue({ callDetail: null, isLoading: true });
    const user = userEvent.setup();
    render(<CallDrawer task={taskFromPage(RUN_DETAIL_PAGE)} agentType="text" onClose={() => {}} />);

    // The removed eval also failed, so it reaches the banner as well as the
    // Evals tab — marked in both, by the test id an e2e flow locates.
    expect(screen.getByText(/no_misselling failed/)).toBeInTheDocument();
    expect(screen.getAllByTestId("removed-eval-marker")).toHaveLength(1);

    await user.click(screen.getByRole("tab", { name: /Evals \(2\)/ }));

    expect(screen.getByText("no_misselling")).toBeInTheDocument();
    // The stored reason, in the banner and in the tab row.
    expect(screen.getAllByText("flagged upsell")).toHaveLength(2);
    expect(screen.getAllByTestId("removed-eval-marker")).toHaveLength(2);
    // The live failing eval is listed too, and never marked — the total stays
    // at one marker per surface, for the removed eval only.
    expect(screen.getByText(/Refund correctness failed/)).toBeInTheDocument();
  });

  // The half of the fixture that is the backend's to keep: drop the removed
  // eval's column from `column_order` and the verdict never reaches the
  // drawer at all, however faithfully the row carries it.
  it("loses that verdict entirely when the endpoint drops the removed eval's column", () => {
    useCallDetail.mockReturnValue({ callDetail: null, isLoading: true });
    const withoutColumn = {
      ...RUN_DETAIL_PAGE,
      column_order: RUN_DETAIL_PAGE.column_order.filter((c) => c.id !== "eval-removed"),
    };
    render(<CallDrawer task={taskFromPage(withoutColumn)} agentType="text" onClose={() => {}} />);

    expect(screen.queryByText(/no_misselling failed/)).toBeNull();
    expect(screen.queryByTestId("removed-eval-marker")).toBeNull();
    // The live eval still comes through — only the removed column is gone.
    expect(screen.getByText(/Refund correctness failed/)).toBeInTheDocument();
  });
});

describe("CallDrawer — voice branch", () => {
  it("routes a voice call to the real product voice drawer, fed the call detail", () => {
    useCallExecutionV3Detail.mockReturnValue({
      data: {
        id: "voice-1",
        scenario_id: "s1",
        transcript: [
          { content: "Checking.", start_time_seconds: 0 },
          { content: "Found it.", start_time_seconds: 10 },
        ],
        function_calls: [
          {
            name: "lookup_order",
            arguments: { order_id: "AB-1" },
            result: { status: "shipped" },
            duration_ms: 309,
            start_time_seconds: 5,
          },
          { name: "untimed_tool", at: 0 },
        ],
      },
      isPending: false,
    });
    render(
      <CallDrawer
        task={{ id: "voice-1", simulationCallType: "voice" }}
        agentType="voice"
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("voice-drawer")).toHaveTextContent(
      "voice:voice-1",
    );
    expect(screen.getByTestId("voice-drawer")).toHaveTextContent(
      "Function call · lookup_order · 309ms",
    );
    expect(screen.getByTestId("voice-drawer")).toHaveTextContent(
      'args: {"order_id":"AB-1"}',
    );
    expect(screen.getByTestId("voice-drawer")).toHaveTextContent(
      'result: {"status":"shipped"}',
    );
    const timeline = screen.getByTestId("voice-drawer").textContent;
    expect(timeline.indexOf("Checking.")).toBeLessThan(
      timeline.indexOf("lookup_order"),
    );
    expect(timeline.indexOf("lookup_order")).toBeLessThan(
      timeline.indexOf("Found it."),
    );
    expect(timeline.indexOf("Found it.")).toBeLessThan(
      timeline.indexOf("untimed_tool"),
    );
  });
});
