// Chat history (the header's clock button, in the sidebar and on the Chat
// page) and a chat that no longer exists (AUD-CHAT-06). Picking it showed an
// empty chat with no notice, and the next message went to the deleted chat
// and came back as a false "workspace unavailable" error.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import FalconAIHeader from "../components/FalconAIHeader";

const h = vi.hoisted(() => ({
  api: { fetchConversations: vi.fn(), getConversation: vi.fn() },
  notify: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.notify }));
vi.mock("../hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "general" }),
}));
vi.mock("../hooks/useFalconAPI", () => h.api);
vi.mock("../components/SkillEditorDialog", () => ({ default: () => null }));

beforeEach(() => {
  useFalconStore.getState().resetAll();
  Object.values(h.api).forEach((fn) => fn.mockReset());
  h.notify.mockReset();
  h.api.fetchConversations.mockResolvedValue({
    results: [
      { id: "gone", title: "Deleted chat" },
      { id: "kept", title: "Kept chat" },
    ],
    has_more: false,
  });
  // An open chat, so a failed pick could leave its messages behind.
  useFalconStore.setState({
    currentConversationId: "kept",
    messages: [{ id: "m1", role: "user", content: "Earlier question" }],
  });
});

async function pickFromHistory(title) {
  render(<FalconAIHeader mode="sidebar" />);
  fireEvent.click(screen.getByTitle("Chat history"));
  fireEvent.click(await screen.findByText(title));
}

describe("FalconAIHeader history: a chat that no longer exists", () => {
  it("says so and starts a new chat instead of an empty one", async () => {
    h.api.getConversation.mockRejectedValueOnce({
      statusCode: 404,
      message: "Conversation not found",
    });

    await pickFromHistory("Deleted chat");

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        "This chat isn't available. It may have been deleted, or it may be in another workspace.",
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(h.api.getConversation).toHaveBeenCalledWith("gone");
    // A new chat: the next message creates a chat instead of using "gone".
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    expect(useFalconStore.getState().messages).toEqual([]);
    expect(h.notify).toHaveBeenCalledTimes(1);
  });

  it("a chat that loads opens with its messages and no notice", async () => {
    h.api.getConversation.mockResolvedValueOnce({
      result: {
        messages: [{ id: "k1", role: "user", content: "Kept question" }],
      },
    });

    await pickFromHistory("Kept chat");

    await waitFor(() =>
      expect(useFalconStore.getState().messages[0]?.content).toBe(
        "Kept question",
      ),
    );
    expect(useFalconStore.getState().currentConversationId).toBe("kept");
    expect(h.notify).not.toHaveBeenCalled();
  });
});

// Picking another chat while an answer was still being written left the
// picked chat's message box disabled behind Stop. That answer's own frames do
// not reach another chat, and Stop there cancelled the answer being left.
describe("FalconAIHeader history: picking a chat while an answer is being written", () => {
  it("gives the picked chat's message box back", async () => {
    useFalconStore.setState({
      isStreaming: true,
      streamingMessageId: "assistant-kept",
    });
    h.api.fetchConversations.mockResolvedValue({
      results: [
        { id: "other", title: "Other chat" },
        { id: "kept", title: "Kept chat" },
      ],
      has_more: false,
    });
    let finishLoad;
    h.api.getConversation.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishLoad = resolve;
        }),
    );

    await pickFromHistory("Other chat");

    // Given back as soon as the chat is picked, before it has loaded.
    expect(useFalconStore.getState()).toMatchObject({
      currentConversationId: "other",
      isStreaming: false,
      streamingMessageId: null,
    });
    await waitFor(() => expect(finishLoad).toBeTypeOf("function"));
    await act(async () =>
      finishLoad({
        result: {
          messages: [{ id: "o1", role: "user", content: "Other question" }],
        },
      }),
    );
    expect(useFalconStore.getState().messages[0]?.content).toBe(
      "Other question",
    );
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(h.notify).not.toHaveBeenCalled();
  });
});
