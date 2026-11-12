// Chat > a reply with no answer (AUD-CHAT-03). A failed reply showed its
// error with no way to ask again, and after a reload (which keeps the empty
// reply but not its error) it showed nothing at all under the question.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import useFalconStore from "../../store/useFalconStore";
import AssistantMessage from "../AssistantMessage";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../TextBlock", () => ({
  default: ({ content }) => (content ? <p>{content}</p> : null),
}));
vi.mock("../ToolCallCard", () => ({ default: () => null }));
vi.mock("../CompletionCard", () => ({
  default: () => <div>Completion card</div>,
}));

const question = { id: "q1", role: "user", content: "Why did run 42 fail?" };
const NO_ANSWER = "Falcon didn't answer this message.";

function show(reply, { streaming = false } = {}) {
  useFalconStore.setState({
    messages: [question, reply],
    isStreaming: streaming,
    streamingMessageId: streaming ? reply.id : null,
  });
  render(<AssistantMessage message={reply} />);
}

beforeEach(() => {
  useFalconStore.getState().resetAll();
});

describe("AssistantMessage without an answer", () => {
  it("a reply saved empty says so, and Ask again puts the question back in the box", () => {
    show({
      id: "r1",
      role: "assistant",
      content: "",
      tool_calls: [],
      blocks: [],
    });

    expect(screen.getByText(NO_ANSWER)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
    expect(useFalconStore.getState().pendingPrompt).toBe(
      "Why did run 42 fail?",
    );
  });

  it("a failed reply keeps its reason, and Ask again puts the question back in the box", () => {
    show({
      id: "r1",
      role: "assistant",
      content: "",
      blocks: [],
      error: "Falcon couldn't answer this time.",
    });

    expect(
      screen.getByText("Falcon couldn't answer this time."),
    ).toBeInTheDocument();
    expect(screen.queryByText(NO_ANSWER)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
    expect(useFalconStore.getState().pendingPrompt).toBe(
      "Why did run 42 fail?",
    );
  });

  it("a reply still being written shows no failure", () => {
    show(
      { id: "r1", role: "assistant", content: "", blocks: [] },
      {
        streaming: true,
      },
    );

    expect(screen.queryByText(NO_ANSWER)).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });

  it("an answered reply shows no failure", () => {
    show({
      id: "r1",
      role: "assistant",
      content: "Run 42 timed out.",
      blocks: [{ type: "text", id: "t1", content: "Run 42 timed out." }],
    });

    expect(screen.getByText("Run 42 timed out.")).toBeInTheDocument();
    expect(screen.queryByText(NO_ANSWER)).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });

  it("a reply holding only a completion card shows no failure", () => {
    show({
      id: "r1",
      role: "assistant",
      content: "",
      blocks: [],
      completion_card: { title: "Dataset created" },
    });

    expect(screen.getByText("Completion card")).toBeInTheDocument();
    expect(screen.queryByText(NO_ANSWER)).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });
});
