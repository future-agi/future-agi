import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The chat drawer's data hook is mocked so the render asserts the drawer wiring
// against a fixed CallDetail rather than the network. `isVoiceCall` (the routing
// decision under test) stays real — it lives in its own `callRouting` module.
const useCallDetail = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useCallDetail: (...a) => useCallDetail(...a) };
});

// The voice branch reuses the real product drawer; stub it to a marker so the
// test proves the routing without mounting the heavy component (imagine store,
// saved views, share dialog).
vi.mock("src/components/VoiceDetailDrawerV2", () => ({
  default: ({ data }) => <div data-testid="voice-drawer">voice:{data?.id}</div>,
}));

// The voice wrapper reads the product call-execution hook; feed it real-shaped data.
const useCallExecutionDetail = vi.fn();
vi.mock("src/sections/agents/helper", () => ({
  useCallExecutionDetail: (...a) => useCallExecutionDetail(...a),
}));

const { default: CallDrawer } = await import("../CallDrawer");
const { isVoiceCall } = await import("../callRouting");

const CHAT_DETAIL = {
  id: "chat-1",
  type: "chat",
  provider: "openai",
  durationS: 30,
  turns: [
    { role: "agent", text: "Refund issued.", toolCalls: [{ function: { name: "issue_refund" } }] },
    { role: "customer", text: "thanks" },
  ],
  stats: { turnCount: 2, latencyMs: 800, aiPct: 50, userPct: 50, words: 3, silenceS: null, ttfwMs: null, toolCalls: 1 },
  tokens: 1200,
  cost: null,
  summary: "ok",
  evalResults: [{ id: "e1", name: "Refund correctness", score: 0, passed: false, reason: "wrong amount" }],
};

const chatTask = {
  id: "chat-1",
  scenario: "Refund a double charge",
  persona: "Impatient caller",
  status: "failed",
  simulationCallType: "text",
  turns: 2,
  provider: "openai",
  evalResults: CHAT_DETAIL.evalResults,
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
    useCallDetail.mockReturnValue({ callDetail: CHAT_DETAIL, isLoading: false });
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
    useCallDetail.mockReturnValue({ callDetail: CHAT_DETAIL, isLoading: false });
    const user = userEvent.setup();
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);

    // The reason shows once in the failed-eval banner already.
    expect(screen.getAllByText("wrong amount")).toHaveLength(1);
    await user.click(screen.getByRole("tab", { name: /Evals \(1\)/ }));
    // The Evals tab adds its own row — banner + tab = two occurrences.
    expect(screen.getAllByText("wrong amount")).toHaveLength(2);
  });

  it("does not mount the product voice drawer for a chat call", () => {
    useCallDetail.mockReturnValue({ callDetail: CHAT_DETAIL, isLoading: false });
    render(<CallDrawer task={chatTask} agentType="text" onClose={() => {}} />);
    expect(screen.queryByTestId("voice-drawer")).toBeNull();
  });
});

describe("CallDrawer — voice branch", () => {
  it("routes a voice call to the real product voice drawer, fed the call detail", () => {
    useCallExecutionDetail.mockReturnValue({ data: { id: "voice-1", scenario_id: "s1" }, isPending: false });
    render(
      <CallDrawer
        task={{ id: "voice-1", simulationCallType: "voice" }}
        agentType="voice"
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("voice-drawer")).toHaveTextContent("voice:voice-1");
  });
});
