import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import {
  fetchConversations,
  getConversation,
  checkStreamStatus,
} from "../hooks/useFalconAPI";
import useFalconStore from "../store/useFalconStore";
import ChatListPanel from "../components/ChatListPanel";
import ChatInput from "../components/ChatInput";

function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = {
  icon: PropTypes.string.isRequired,
};

vi.mock("src/components/iconify", () => ({
  default: MockIconify,
}));

vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("../components/ContextSelector", () => ({
  default: () => <div data-testid="context-selector" />,
}));

vi.mock("../components/SlashCommandPicker", () => ({
  default: () => <div data-testid="slash-command-picker" />,
}));

vi.mock("../hooks/useFalconAPI", () => ({
  fetchConversations: vi.fn(),
  getConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  checkStreamStatus: vi.fn(),
  uploadFile: vi.fn(),
}));

const CONV = {
  id: "11111111-1111-4111-8111-111111111111",
  title: "Finished chat",
  updated_at: new Date().toISOString(),
};

function renderSidebarAndComposer() {
  return render(
    <>
      <ChatListPanel />
      <ChatInput onSend={vi.fn()} onStop={vi.fn()} />
    </>,
  );
}

async function openConversation(streamStatus) {
  vi.mocked(checkStreamStatus).mockResolvedValue({
    status: true,
    result: { stream_status: streamStatus },
  });
  renderSidebarAndComposer();
  fireEvent.click(await screen.findByText(CONV.title));
  await waitFor(() => expect(checkStreamStatus).toHaveBeenCalledWith(CONV.id));
  await waitFor(() =>
    expect(useFalconStore.getState().messages).toHaveLength(2),
  );
}

beforeEach(() => {
  useFalconStore.getState().resetAll();
  vi.mocked(fetchConversations).mockReset();
  vi.mocked(getConversation).mockReset();
  vi.mocked(checkStreamStatus).mockReset();
  vi.mocked(fetchConversations).mockResolvedValue({
    results: [CONV],
    has_more: false,
  });
  vi.mocked(getConversation).mockResolvedValue({
    status: true,
    result: {
      id: CONV.id,
      messages: [
        { id: "m1", role: "user", content: "Hello" },
        { id: "m2", role: "assistant", content: "Hi there" },
      ],
    },
  });
});

describe("ChatListPanel reopening a conversation", () => {
  it.each(["done", "none", "error", "cancelled"])(
    "keeps the message box usable when the stream status is %s",
    async (streamStatus) => {
      await openConversation(streamStatus);
      expect(useFalconStore.getState().isStreaming).toBe(false);
      expect(
        screen.getByPlaceholderText("Ask a follow-up..."),
      ).not.toBeDisabled();
      expect(screen.queryByTitle("Stop")).not.toBeInTheDocument();
      expect(screen.getByTitle("Send")).toBeInTheDocument();
    },
  );

  it("unlocks the message box when switching from a streaming chat to a finished one", async () => {
    useFalconStore.getState().setStreaming(true, "other-message");
    await openConversation("done");
    await waitFor(() =>
      expect(useFalconStore.getState().isStreaming).toBe(false),
    );
    expect(
      screen.getByPlaceholderText("Ask a follow-up..."),
    ).not.toBeDisabled();
    expect(screen.queryByTitle("Stop")).not.toBeInTheDocument();
  });

  it("still locks the message box while the reply is running", async () => {
    await openConversation("running");
    await waitFor(() =>
      expect(useFalconStore.getState().isStreaming).toBe(true),
    );
    expect(screen.getByPlaceholderText("Ask a follow-up...")).toBeDisabled();
    expect(screen.getByTitle("Stop")).toBeInTheDocument();
  });
});
