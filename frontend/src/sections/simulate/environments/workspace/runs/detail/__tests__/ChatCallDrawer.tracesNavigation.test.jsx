/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

// The real Traces panel, with only the tracing store and its drawer stubbed, so a trace it opens is recorded.
const mocks = vi.hoisted(() => ({
  opened: [],
  traces: { projectId: "p1", traces: [], isError: false },
}));

vi.mock("src/sections/projects/LLMTracing/states", () => ({
  useLLMTracingStoreShallow: (selector) =>
    selector({
      setTraceDetailDrawerOpen: (value) => mocks.opened.push(value),
      setVisibleTraces: () => {},
      resetStates: () => {},
    }),
}));
vi.mock("src/sections/projects/LLMTracing/LLMTracingTraceDetailDrawer", () => ({
  default: () => null,
}));
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: () => ({ callDetail: null, isLoading: false }),
    useHasWorkers: () => true,
    useCallTraces: () => mocks.traces,
  };
});
vi.mock("../CallWorkersPanel", () => ({
  default: ({ onOpenTrace }) => (
    <button type="button" onClick={() => onOpenTrace("tr-a")}>
      open step trace
    </button>
  ),
}));

const { default: ChatCallDrawer } = await import("../ChatCallDrawer");

describe("ChatCallDrawer Traces across calls", () => {
  it("never reopens call A's trace on call B as B's traces load", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <ChatCallDrawer
        task={{ id: "call-a", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Participants" }));
    await user.click(screen.getByRole("button", { name: "open step trace" }));
    expect(mocks.opened.filter((one) => one?.traceId === "tr-a")).toHaveLength(
      1,
    );

    // Next: call B, with the Traces tab still open; its traces are unread, then load.
    mocks.traces = { projectId: null, traces: [], isError: false };
    rerender(
      <ChatCallDrawer
        task={{ id: "call-b", status: "completed" }}
        onClose={vi.fn()}
      />,
    );
    mocks.traces = { projectId: "p1", traces: [], isError: false };
    rerender(
      <ChatCallDrawer
        task={{ id: "call-b", status: "completed" }}
        onClose={vi.fn()}
      />,
    );

    expect(mocks.opened.filter((one) => one?.traceId === "tr-a")).toHaveLength(
      1,
    );
  });
});
