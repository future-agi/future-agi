// Chat > chat list, Rename and Delete (owner review). Rename used the
// browser's own window.prompt box, Delete removed the chat on one click, and
// a rename or delete that failed on the server showed nothing.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import ChatListPanel from "../components/ChatListPanel";
import ChatInput from "../components/ChatInput";
import useFalconStore from "../store/useFalconStore";

const h = vi.hoisted(() => ({
  api: {
    fetchConversations: vi.fn(),
    getConversation: vi.fn(),
    deleteConversation: vi.fn(),
    renameConversation: vi.fn(),
    checkStreamStatus: vi.fn(),
    // ChatInput (rendered beside the list below) imports it.
    uploadFile: vi.fn(),
  },
  notify: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../hooks/useFalconAPI", () => h.api);
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.notify }));
vi.mock("notistack", () => ({ enqueueSnackbar: h.notify }));
// ChatInput's own menus; the message box and its Send/Stop button stay real.
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("../components/ContextSelector", () => ({ default: () => null }));
vi.mock("../components/SlashCommandPicker", () => ({ default: () => null }));

// A small fake server: the list endpoint returns what rename/delete left, so
// the list reload that follows mount never brings back a stale list.
let server = [];
function installServer() {
  const now = new Date().toISOString();
  server = [
    { id: "a", title: "Chat A", updated_at: now },
    { id: "b", title: "Chat B", updated_at: now },
  ];
  Object.values(h.api).forEach((fn) => fn.mockReset());
  h.api.fetchConversations.mockImplementation(async () => ({
    results: server.map((c) => ({ ...c })),
    has_more: false,
  }));
  h.api.renameConversation.mockImplementation(async (id, title) => {
    server = server.map((c) => (c.id === id ? { ...c, title } : c));
    return { status: true, result: server.find((c) => c.id === id) };
  });
  h.api.deleteConversation.mockImplementation(async (id) => {
    server = server.filter((c) => c.id !== id);
  });
  h.api.getConversation.mockResolvedValue({ result: { messages: [] } });
  h.api.checkStreamStatus.mockResolvedValue({ result: {} });
}

async function renderPanel() {
  render(<ChatListPanel />);
  // Mount loads the list, then the search debounce loads it once more
  // (~300 ms). Interact only after both have landed.
  await waitFor(() =>
    expect(h.api.fetchConversations).toHaveBeenCalledTimes(2),
  );
  await waitFor(() => expect(screen.queryByRole("progressbar")).toBeNull());
  await screen.findByText("Chat A");
}

// The row's three-dot button (it is the only button in the row).
function openRowMenu(title) {
  const row = screen.getByText(title).parentElement;
  fireEvent.click(within(row).getByRole("button"));
}

function chooseMenuItem(name) {
  fireEvent.click(screen.getByRole("menuitem", { name }));
}

async function openRename(title) {
  openRowMenu(title);
  chooseMenuItem("Rename");
  const dialog = await screen.findByRole("dialog", { name: "Rename chat" });
  const input = within(dialog).getByRole("textbox", { name: "Chat name" });
  return { dialog, input };
}

async function openDelete(title) {
  openRowMenu(title);
  chooseMenuItem("Delete");
  return screen.findByRole("dialog", { name: `Delete "${title}"?` });
}

const dialogGone = () =>
  waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

let prompt;
beforeEach(() => {
  useFalconStore.getState().resetAll();
  installServer();
  h.notify.mockReset();
  prompt = vi.spyOn(window, "prompt").mockReturnValue(null);
});

afterEach(() => {
  prompt.mockRestore();
});

describe("ChatListPanel rename", () => {
  it("renames in the product's own dialog; Enter saves the new name", async () => {
    await renderPanel();
    openRowMenu("Chat A");
    chooseMenuItem("Rename");
    expect(prompt).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog", { name: "Rename chat" });
    const input = within(dialog).getByRole("textbox", { name: "Chat name" });
    expect(input).toHaveValue("Chat A");

    fireEvent.change(input, { target: { value: "  Launch notes  " } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(h.api.renameConversation).toHaveBeenCalledWith(
        "a",
        "Launch notes",
      ),
    );
    expect(await screen.findByText("Launch notes")).toBeInTheDocument();
    expect(screen.queryByText("Chat A")).toBeNull();
    await dialogGone();
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("Escape closes the dialog without saving", async () => {
    await renderPanel();
    const { input } = await openRename("Chat A");

    fireEvent.change(input, { target: { value: "Something else" } });
    fireEvent.keyDown(input, { key: "Escape" });

    await dialogGone();
    expect(h.api.renameConversation).not.toHaveBeenCalled();
    expect(screen.getByText("Chat A")).toBeInTheDocument();
  });

  it("never sends an empty or unchanged name", async () => {
    await renderPanel();
    const { dialog, input } = await openRename("Chat A");
    const save = within(dialog).getByRole("button", { name: "Save" });

    // Unchanged.
    expect(save).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });

    // Empty (spaces only).
    fireEvent.change(input, { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });

    // The same name with spaces around it.
    fireEvent.change(input, { target: { value: " Chat A " } });
    expect(save).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });

    expect(h.api.renameConversation).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Rename chat" }),
    ).toBeInTheDocument();
  });

  it("a failed rename says so, keeps the old name and keeps what was typed", async () => {
    h.api.renameConversation.mockRejectedValueOnce({ statusCode: 500 });
    await renderPanel();
    const { dialog, input } = await openRename("Chat A");
    const save = within(dialog).getByRole("button", { name: "Save" });

    fireEvent.change(input, { target: { value: "Launch notes" } });
    fireEvent.click(save);

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't rename the chat/i),
        expect.objectContaining({ variant: "error" }),
      ),
    );
    // The list keeps the old name.
    expect(screen.getByText("Chat A")).toBeInTheDocument();
    expect(screen.queryByText("Launch notes")).toBeNull();
    // The dialog stays open (not just fading out) with what was typed.
    await expect(
      waitFor(() => expect(screen.queryByRole("dialog")).toBeNull(), {
        timeout: 500,
      }),
    ).rejects.toThrow();
    expect(
      screen.getByRole("dialog", { name: "Rename chat" }),
    ).toBeInTheDocument();
    expect(input).toHaveValue("Launch notes");
    await waitFor(() => expect(save).toBeEnabled());

    // Save again: this time the server takes it.
    fireEvent.click(save);

    expect(await screen.findByText("Launch notes")).toBeInTheDocument();
    expect(screen.queryByText("Chat A")).toBeNull();
    await dialogGone();
    expect(h.api.renameConversation).toHaveBeenCalledTimes(2);
    expect(h.notify).toHaveBeenCalledTimes(1);
  });
});

describe("ChatListPanel delete", () => {
  it("asks first, naming the chat; Keep leaves it", async () => {
    await renderPanel();
    openRowMenu("Chat A");
    chooseMenuItem("Delete");
    expect(h.api.deleteConversation).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog", {
      name: 'Delete "Chat A"?',
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep" }));

    await dialogGone();
    expect(h.api.deleteConversation).not.toHaveBeenCalled();
    expect(screen.getByText("Chat A")).toBeInTheDocument();
  });

  it("Delete in the dialog removes the chat; deleting the open chat starts a new one", async () => {
    useFalconStore.setState({ currentConversationId: "a" });
    await renderPanel();
    const dialog = await openDelete("Chat A");

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByText("Chat A")).toBeNull());
    expect(h.api.deleteConversation).toHaveBeenCalledWith("a");
    expect(screen.getByText("Chat B")).toBeInTheDocument();
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    await dialogGone();
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("a failed delete says so and keeps the chat", async () => {
    h.api.deleteConversation.mockRejectedValueOnce({ statusCode: 500 });
    await renderPanel();
    const dialog = await openDelete("Chat A");

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        expect.stringMatching(/couldn't delete the chat/i),
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(screen.getByText("Chat A")).toBeInTheDocument();
    await dialogGone();
  });

  it("a chat already gone on the server leaves the list, with no error", async () => {
    await renderPanel();
    server = server.filter((c) => c.id !== "a");
    h.api.deleteConversation.mockRejectedValueOnce({ statusCode: 404 });
    const dialog = await openDelete("Chat A");

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByText("Chat A")).toBeNull());
    expect(screen.getByText("Chat B")).toBeInTheDocument();
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("adds no second message when a workspace reset already explained the failure", async () => {
    await renderPanel();
    // The workspace-unavailable recovery resets the chat store (and shows its
    // own message) before the failed request settles.
    h.api.deleteConversation.mockImplementationOnce(async () => {
      useFalconStore.getState().resetAll();
      throw Object.assign(new Error("Workspace unavailable"), {
        statusCode: 403,
        code: "workspace_unavailable",
      });
    });
    const dialog = await openDelete("Chat A");

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(h.api.deleteConversation).toHaveBeenCalled());
    await dialogGone();
    expect(h.notify).not.toHaveBeenCalled();
  });
});

// Chat > reopening a chat from the list (AUD-CHAT-01). For 5 minutes after an
// answer finishes the server still reports it as "done", and opening that
// chat again greyed out the message box behind a red Stop button although
// nothing was running. An answer still being written locked the box too, but
// never showed up.
describe("ChatListPanel reopening a chat", () => {
  const question = {
    id: "q1",
    role: "user",
    content: "Which runs failed today?",
  };
  const answer = { id: "r1", role: "assistant", content: "Two runs failed." };

  // Opens Chat A beside the real message box; the stream status answers only
  // once the conversation has loaded, as on the server. beforeStatus runs
  // after the chat has loaded and before its stream status answers.
  async function reopen(status, messages, beforeStatus) {
    const resume = vi.fn();
    let answerStatus;
    h.api.getConversation.mockResolvedValue({ result: { messages } });
    h.api.checkStreamStatus.mockImplementation(
      () =>
        new Promise((resolve) => {
          answerStatus = resolve;
        }),
    );
    render(
      <>
        <ChatListPanel onResume={resume} />
        <ChatInput onSend={vi.fn()} onStop={vi.fn()} />
      </>,
    );
    await waitFor(() =>
      expect(h.api.fetchConversations).toHaveBeenCalledTimes(2),
    );
    await waitFor(() => expect(screen.queryByRole("progressbar")).toBeNull());
    fireEvent.click(await screen.findByText("Chat A"));
    await waitFor(() =>
      expect(h.api.checkStreamStatus).toHaveBeenCalledWith("a"),
    );
    beforeStatus?.();
    await act(async () => answerStatus({ result: { stream_status: status } }));
    return resume;
  }

  it("a chat whose answer finished keeps its message box usable", async () => {
    const resume = await reopen("done", [question, answer]);

    expect(screen.getByPlaceholderText("Ask a follow-up...")).toBeEnabled();
    expect(screen.queryByTitle("Stop")).toBeNull();
    expect(screen.getByTitle("Send")).toBeInTheDocument();
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(useFalconStore.getState().messages).toHaveLength(2);
    expect(resume).not.toHaveBeenCalled();
  });

  it("a chat still being answered gets a reply that fills in, with Stop meanwhile", async () => {
    const resume = await reopen("running", [question]);

    const { messages, isStreaming, streamingMessageId } =
      useFalconStore.getState();
    expect(messages).toHaveLength(2);
    expect(messages[1]).toMatchObject({ role: "assistant", content: "" });
    // Replayed and live events land in this reply.
    expect(isStreaming).toBe(true);
    expect(streamingMessageId).toBe(messages[1].id);
    expect(resume).toHaveBeenCalledTimes(1);
    expect(resume).toHaveBeenCalledWith("a");
    expect(screen.getByTitle("Stop")).toBeInTheDocument();
  });

  it("an answer already saved is not locked while its status catches up", async () => {
    const resume = await reopen("running", [question, answer]);

    expect(useFalconStore.getState().messages).toHaveLength(2);
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(resume).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText("Ask a follow-up...")).toBeEnabled();
  });

  // Choosing another chat while an answer is still being written left the
  // chosen chat's box disabled behind Stop. That answer's own frames do not
  // reach another chat, and Stop there cancelled the answer being left.
  it("choosing another chat while an answer is being written gives its message box back", async () => {
    useFalconStore.setState({
      currentConversationId: "b",
      messages: [
        { id: "qb", role: "user", content: "Summarize Chat B" },
        { id: "assistant-b", role: "assistant", content: "Chat B has" },
      ],
      isStreaming: true,
      streamingMessageId: "assistant-b",
    });

    const resume = await reopen("done", [question, answer], () => {
      // Given back as soon as the chat is chosen, before its status answers.
      expect(useFalconStore.getState()).toMatchObject({
        currentConversationId: "a",
        isStreaming: false,
        streamingMessageId: null,
      });
    });

    expect(screen.getByPlaceholderText("Ask a follow-up...")).toBeEnabled();
    expect(screen.queryByTitle("Stop")).toBeNull();
    expect(screen.getByTitle("Send")).toBeInTheDocument();
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(useFalconStore.getState().messages).toHaveLength(2);
    expect(resume).not.toHaveBeenCalled();
  });

  it("choosing the chat being answered again picks its answer up again", async () => {
    // Chat A is answering into a reply that reloading the chat drops.
    useFalconStore.setState({
      currentConversationId: "a",
      messages: [
        question,
        { id: "assistant-old", role: "assistant", content: "Two" },
      ],
      isStreaming: true,
      streamingMessageId: "assistant-old",
    });

    const resume = await reopen("running", [question]);

    const { messages, isStreaming, streamingMessageId } =
      useFalconStore.getState();
    expect(messages).toHaveLength(2);
    expect(messages[1]).toMatchObject({ role: "assistant", content: "" });
    expect(isStreaming).toBe(true);
    expect(streamingMessageId).toBe(messages[1].id);
    expect(streamingMessageId).not.toBe("assistant-old");
    expect(resume).toHaveBeenCalledWith("a");
    expect(screen.getByTitle("Stop")).toBeInTheDocument();
  });
});

// Chat > a chat deleted elsewhere (AUD-CHAT-06). Opening it from the list
// showed an empty "How can I help?" chat with no notice, and the next
// message went to the deleted chat and came back as a false "workspace
// unavailable" error.
describe("ChatListPanel chat that no longer exists", () => {
  it("says so, drops it from the list and starts a new chat", async () => {
    useFalconStore.setState({ currentConversationId: "b" });
    await renderPanel();
    // Deleted in another tab: the server no longer has it.
    server = server.filter((c) => c.id !== "a");
    h.api.getConversation.mockRejectedValueOnce({
      statusCode: 404,
      message: "Conversation not found",
    });

    fireEvent.click(screen.getByText("Chat A"));

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        "This chat isn't available. It may have been deleted, or it may be in another workspace.",
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(h.api.getConversation).toHaveBeenCalledWith("a");
    await waitFor(() => expect(screen.queryByText("Chat A")).toBeNull());
    expect(screen.getByText("Chat B")).toBeInTheDocument();
    // A new chat: the next message creates a chat instead of using "a".
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    expect(useFalconStore.getState().messages).toEqual([]);
    expect(h.api.checkStreamStatus).not.toHaveBeenCalled();
    expect(h.notify).toHaveBeenCalledTimes(1);
  });

  it("any other load failure keeps the chat selected and adds no notice", async () => {
    await renderPanel();
    h.api.getConversation.mockRejectedValueOnce({ statusCode: 500 });

    fireEvent.click(screen.getByText("Chat A"));

    await waitFor(() =>
      expect(h.api.getConversation).toHaveBeenCalledWith("a"),
    );
    await act(async () => {});
    expect(useFalconStore.getState().currentConversationId).toBe("a");
    expect(screen.getByText("Chat A")).toBeInTheDocument();
    expect(h.notify).not.toHaveBeenCalled();
  });
});
