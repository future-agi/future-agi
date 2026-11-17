// The chat sidebar on every page. A first message whose chat could not be
// created vanished from the box with no word, while the full Chat page puts
// it back and says it wasn't sent. The sidebar now does the same, in the
// same words.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import FalconAISidebar from "../FalconAISidebar";
import useFalconStore from "../store/useFalconStore";

const h = vi.hoisted(() => ({
  api: {
    createConversation: vi.fn(),
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
  }),
}));
vi.mock("../hooks/useFalconCatalog", () => ({ default: () => vi.fn() }));
vi.mock("../hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "general" }),
}));
// The message box (ChatInput) and the sidebar's send logic are real.
vi.mock("../components/FalconAIHeader", () => ({ default: () => null }));
vi.mock("../components/MessageList", () => ({ default: () => null }));
vi.mock("../components/SkillPicker", () => ({ default: () => null }));
vi.mock("../components/ContextSelector", () => ({ default: () => null }));
vi.mock("../components/SlashCommandPicker", () => ({ default: () => null }));

// The full Chat page's sentence for the same failure.
const NOT_SENT = "Couldn't send your message. Try again.";
const MESSAGE = "Summarize yesterday's failed runs";

const box = () => screen.getByPlaceholderText("Ask anything...");

function renderSidebar() {
  useFalconStore.getState().openSidebar();
  return render(
    <MemoryRouter initialEntries={["/dashboard/overview"]}>
      <FalconAISidebar />
    </MemoryRouter>,
  );
}

function send(text) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

beforeEach(() => {
  useFalconStore.getState().resetAll();
  Object.values(h.api).forEach((fn) => fn.mockReset());
  h.sendChat.mockReset();
  h.notify.mockReset();
});

describe("FalconAISidebar first message whose chat could not be created", () => {
  it("puts the message back in the box and says it wasn't sent", async () => {
    h.api.createConversation.mockRejectedValueOnce({
      statusCode: 500,
      message: "Something went wrong",
    });
    renderSidebar();

    send(MESSAGE);

    await waitFor(() =>
      expect(h.notify).toHaveBeenCalledWith(
        NOT_SENT,
        expect.objectContaining({ variant: "error" }),
      ),
    );
    await waitFor(() => expect(box()).toHaveValue(MESSAGE));
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(h.sendChat).not.toHaveBeenCalled();
    expect(useFalconStore.getState().messages).toEqual([]);
    expect(useFalconStore.getState().pendingDraft).toBeNull();
  });

  it("after an upgrade-required answer, which shows its own message, the text still comes back", async () => {
    h.api.createConversation.mockRejectedValueOnce({
      statusCode: 402,
      upgrade_required: true,
      error: "Not available on OSS. Upgrade your plan.",
    });
    renderSidebar();

    send(MESSAGE);

    await waitFor(() => expect(box()).toHaveValue(MESSAGE));
    expect(h.notify).not.toHaveBeenCalled();
    expect(h.sendChat).not.toHaveBeenCalled();
  });

  it("the message put back sends once the chat can be created", async () => {
    h.api.createConversation
      .mockRejectedValueOnce({ statusCode: 500, message: "Down" })
      .mockResolvedValueOnce({ result: { id: "fresh" } });
    renderSidebar();

    send(MESSAGE);
    await waitFor(() => expect(box()).toHaveValue(MESSAGE));
    fireEvent.keyDown(box(), { key: "Enter" });

    await waitFor(() => expect(h.sendChat).toHaveBeenCalledTimes(1));
    expect(h.sendChat.mock.calls[0][0]).toBe(MESSAGE);
    expect(h.sendChat.mock.calls[0][1]).toBe("fresh");
    // The chat now exists, so the box asks for a follow-up, and is empty.
    expect(screen.getByPlaceholderText("Ask a follow-up...")).toHaveValue("");
  });
});
