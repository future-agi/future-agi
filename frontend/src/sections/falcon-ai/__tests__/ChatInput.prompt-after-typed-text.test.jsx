// Ask again and the quick actions put a prompt in the message box. They used
// to replace whatever the person had typed there; now the prompt goes after
// it, on a new line, and an empty box takes the prompt as before.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import ChatInput from "../components/ChatInput";
import AssistantMessage from "../components/AssistantMessage";
import QuickActions from "../components/QuickActions";

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("../components/ContextSelector", () => ({ default: () => null }));
vi.mock("../components/SlashCommandPicker", () => ({ default: () => null }));
vi.mock("../components/AttachedFileChip", () => ({ default: () => null }));
vi.mock("../hooks/useFalconAPI", () => ({ uploadFile: vi.fn() }));
vi.mock("../components/TextBlock", () => ({
  default: ({ content }) => (content ? <p>{content}</p> : null),
}));
vi.mock("../components/ToolCallCard", () => ({ default: () => null }));
vi.mock("../components/CompletionCard", () => ({ default: () => null }));

const ASKED = { id: "q1", role: "user", content: "Why did run 42 fail?" };
const FAILED = {
  id: "r1",
  role: "assistant",
  content: "",
  blocks: [],
  error: "Something went wrong.",
};
const TYPED = "Also check the runs from Monday";

const box = () => screen.getByPlaceholderText("Ask anything...");

const type = (value) => fireEvent.change(box(), { target: { value } });

const askAgain = () =>
  fireEvent.click(screen.getByRole("button", { name: "Ask again" }));

// A failed answer with its Ask again button, above the message box.
function renderFailedAnswer(onSend = vi.fn()) {
  useFalconStore.setState({ messages: [ASKED, FAILED] });
  render(
    <>
      <AssistantMessage message={FAILED} />
      <ChatInput onSend={onSend} />
    </>,
  );
}

beforeEach(() => {
  useFalconStore.getState().resetAll();
});

describe("ChatInput keeps what was typed when a prompt is put in the box", () => {
  it("Ask again adds the message after the typed text, on a new line", async () => {
    renderFailedAnswer();
    type(TYPED);

    askAgain();

    expect(box()).toHaveValue(`${TYPED}\n${ASKED.content}`);
    // The box is focused with the caret after what was added.
    await waitFor(() => expect(document.activeElement).toBe(box()));
    expect(box().selectionStart).toBe(box().value.length);
  });

  it("a quick action adds its prompt after the typed text, on a new line", () => {
    // The chat pages hand a quick action's prompt to the box this way.
    const onAction = vi.fn((prompt) =>
      useFalconStore.getState().setPendingPrompt(prompt),
    );
    render(
      <>
        <QuickActions onAction={onAction} />
        <ChatInput onSend={vi.fn()} />
      </>,
    );
    type(TYPED);

    fireEvent.click(screen.getByText("Analyse my error feed"));

    const [prompt] = onAction.mock.calls[0];
    expect(box()).toHaveValue(`${TYPED}\n${prompt}`);
  });

  it("sends the typed text and the added message together", () => {
    const onSend = vi.fn();
    renderFailedAnswer(onSend);
    type(TYPED);
    askAgain();

    fireEvent.keyDown(box(), { key: "Enter", shiftKey: false });

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith(`${TYPED}\n${ASKED.content}`);
  });

  it("Ask again pressed twice adds the message once", () => {
    renderFailedAnswer();
    type(TYPED);

    askAgain();
    askAgain();

    expect(box()).toHaveValue(`${TYPED}\n${ASKED.content}`);
  });

  it("adds no empty line when the typed text already ends with one", () => {
    renderFailedAnswer();
    type(`${TYPED}\n`);

    askAgain();

    expect(box()).toHaveValue(`${TYPED}\n${ASKED.content}`);
  });

  it("an empty box takes the message as it is", () => {
    renderFailedAnswer();

    askAgain();

    expect(box()).toHaveValue(ASKED.content);
  });

  it("a box holding only spaces takes the message as it is", () => {
    renderFailedAnswer();
    type("   ");

    askAgain();

    expect(box()).toHaveValue(ASKED.content);
  });

  // A skill runs only when the message starts with its command, so a picked
  // skill still fills the box as before.
  it("a skill command still starts the box", () => {
    render(<ChatInput onSend={vi.fn()} />);
    type(TYPED);

    act(() => useFalconStore.getState().setPendingPrompt("/bug-triage "));

    expect(box()).toHaveValue("/bug-triage ");
  });
});
