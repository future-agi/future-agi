// Chat page. A link to a chat that no longer exists (AUD-CHAT-06) opened an
// empty chat with no notice, and the next message went to the deleted chat
// and came back as a false "workspace unavailable" error. A first message
// whose chat could not be created (AUD-CHAT-03) vanished from the box with no
// word.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import FalconAIFullPage from "../FalconAIFullPage";
import useFalconStore from "../store/useFalconStore";

const h = vi.hoisted(() => ({
  api: {
    createConversation: vi.fn(),
    getConversation: vi.fn(),
    uploadFile: vi.fn(),
  },
  sendChat: vi.fn(),
  notify: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.notify }));
vi.mock("notistack", () => ({ enqueueSnackbar: h.notify }));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("../hooks/useFalconAPI", () => h.api);
vi.mock("../hooks/useFalconSocket", () => ({
  default: () => ({
    sendChat: h.sendChat,
    sendStop: vi.fn(),
    sendFeedback: vi.fn(),
    sendReconnect: vi.fn(),
  }),
}));
vi.mock("../hooks/useFalconCatalog", () => ({ default: () => vi.fn() }));
vi.mock("../hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "general" }),
}));
// The message box (ChatInput) and the page's send and load logic are real.
vi.mock("../components/FalconAIHeader", () => ({ default: () => null }));
vi.mock("../components/ChatListPanel", () => ({ default: () => null }));
vi.mock("../components/MessageList", () => ({ default: () => null }));
vi.mock("../components/SkillPicker", () => ({ default: () => null }));
vi.mock("../components/CustomizePanel", () => ({ default: () => null }));
vi.mock("../components/ContextSelector", () => ({ default: () => null }));
vi.mock("../components/SlashCommandPicker", () => ({ default: () => null }));

function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

function renderPage(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Location />
      <Routes>
        <Route
          path="/dashboard/falcon-ai/:conversationId?"
          element={<FalconAIFullPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function send(text) {
  const box = screen.getByPlaceholderText("Message Falcon AI...");
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

beforeEach(() => {
  useFalconStore.getState().resetAll();
  Object.values(h.api).forEach((fn) => fn.mockReset());
  h.sendChat.mockReset();
  h.notify.mockReset();
});

describe("FalconAIFullPage link to a chat that no longer exists", () => {
  it("says so, leaves the dead link, and the next message starts a new chat", async () => {
    h.api.getConversation.mockRejectedValue({
      statusCode: 404,
      message: "Conversation not found",
    });
    h.api.createConversation.mockResolvedValue({
      result: { id: "fresh", title: "Which runs failed today?" },
    });
    renderPage("/dashboard/falcon-ai/deleted-chat");

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        "This chat isn't available. It may have been deleted, or it may be in another workspace.",
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(h.api.getConversation).toHaveBeenCalledWith("deleted-chat");
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        /^\/dashboard\/falcon-ai$/,
      ),
    );

    send("Which runs failed today?");

    await waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(1));
    expect(h.api.createConversation).toHaveBeenCalledTimes(1);
    expect(h.sendChat.mock.calls[0][0]).toBe("Which runs failed today?");
    expect(h.sendChat.mock.calls[0][1]).toBe("fresh");
    expect(h.notify).toHaveBeenCalledTimes(1);
  });

  it("any other load failure keeps the link and adds no notice", async () => {
    h.api.getConversation.mockRejectedValue({ statusCode: 500 });
    renderPage("/dashboard/falcon-ai/kept-chat");

    await waitFor(() =>
      expect(h.api.getConversation).toHaveBeenCalledWith("kept-chat"),
    );
    await waitFor(() =>
      expect(useFalconStore.getState().currentConversationId).toBe("kept-chat"),
    );
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/falcon-ai/kept-chat",
    );
    expect(h.notify).not.toHaveBeenCalled();
  });
});

describe("FalconAIFullPage first message whose chat could not be created", () => {
  it("puts the message back in the box and says it wasn't sent", async () => {
    h.api.createConversation.mockRejectedValueOnce({
      statusCode: 500,
      message: "Something went wrong",
    });
    renderPage("/dashboard/falcon-ai");

    send("Summarize yesterday's failed runs");

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        "Couldn't send your message. Try again.",
        expect.objectContaining({ variant: "error" }),
      ),
    );
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Message Falcon AI...")).toHaveValue(
        "Summarize yesterday's failed runs",
      ),
    );
    expect(h.sendChat).not.toHaveBeenCalled();
    expect(useFalconStore.getState().pendingDraft).toBeNull();
  });

  it("after an upgrade-required answer, which shows its own message, the text still comes back", async () => {
    h.api.createConversation.mockRejectedValueOnce({
      statusCode: 402,
      upgrade_required: true,
      error: "Not available on OSS. Upgrade your plan.",
    });
    renderPage("/dashboard/falcon-ai");

    send("Summarize yesterday's failed runs");

    await waitFor(() =>
      expect(screen.getByPlaceholderText("Message Falcon AI...")).toHaveValue(
        "Summarize yesterday's failed runs",
      ),
    );
    expect(h.notify).not.toHaveBeenCalled();
    expect(h.sendChat).not.toHaveBeenCalled();
  });
});

// A workspace reset (the workspace-unavailable recovery) starts the chat over
// in the next workspace. Text in the message box must not carry over into it,
// and a draft the reset keeps on purpose must still come back.
describe("FalconAIFullPage message box across a workspace reset", () => {
  it("a message put back after a failed send does not carry into the next workspace", async () => {
    h.api.createConversation.mockRejectedValueOnce({
      statusCode: 500,
      message: "Something went wrong",
    });
    renderPage("/dashboard/falcon-ai");

    send("Summarize yesterday's failed runs");
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Message Falcon AI...")).toHaveValue(
        "Summarize yesterday's failed runs",
      ),
    );

    // A recovery that keeps no draft.
    act(() => useFalconStore.getState().resetAll());

    expect(screen.getByPlaceholderText("Message Falcon AI...")).toHaveValue("");
  });

  it("a draft the reset keeps comes back in the box", async () => {
    renderPage("/dashboard/falcon-ai");
    fireEvent.change(screen.getByPlaceholderText("Message Falcon AI..."), {
      target: { value: "Half-typed note" },
    });

    act(() =>
      useFalconStore
        .getState()
        .resetAll({ draft: { text: "Keep my first prompt", files: [] } }),
    );

    await waitFor(() =>
      expect(screen.getByPlaceholderText("Message Falcon AI...")).toHaveValue(
        "Keep my first prompt",
      ),
    );
    expect(useFalconStore.getState().pendingPrompt).toBeNull();
  });
});
