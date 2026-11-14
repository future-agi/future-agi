import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SnackbarProvider } from "notistack";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import { uploadFile } from "../hooks/useFalconAPI";
import useFalconStore from "../store/useFalconStore";
import ChatInput from "../components/ChatInput";

// Mock Iconify
function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = {
  icon: PropTypes.string.isRequired,
};

vi.mock("src/components/iconify", () => ({
  default: MockIconify,
}));

// Mock useRouter
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// Mock ContextSelector
vi.mock("../components/ContextSelector", () => ({
  default: () => <div data-testid="context-selector">ContextSelector</div>,
}));

// Mock SlashCommandPicker
vi.mock("../components/SlashCommandPicker", () => ({
  default: () => <div data-testid="slash-command-picker" />,
}));

// Mock AttachedFileChip
vi.mock("../components/AttachedFileChip", () => ({
  default: ({ file, onRemove: _onRemove }) => (
    <div data-testid="attached-file-chip">{file.name}</div>
  ),
}));

// Mock uploadFile
vi.mock("../hooks/useFalconAPI", () => ({
  uploadFile: vi.fn(),
}));

beforeEach(() => {
  useFalconStore.getState().resetAll();
  vi.mocked(uploadFile).mockReset();
});

describe("ChatInput", () => {
  it.each(["picker", "drop"])(
    "shows the storage configuration error from %s and allows a retry",
    async (method) => {
      const detail =
        "File upload failed because the server storage endpoint is not configured correctly. Contact your administrator.";
      vi.mocked(uploadFile).mockRejectedValueOnce({
        status: false,
        statusCode: 503,
        type: "service_unavailable",
        code: "storage_configuration_error",
        detail,
      });
      const { container } = render(
        <SnackbarProvider>
          <ChatInput onSend={vi.fn()} />
        </SnackbarProvider>,
      );
      const file = new File(["Synthetic notes"], "notes.txt", {
        type: "text/plain",
      });
      const select = () => {
        if (method === "picker") {
          fireEvent.change(container.querySelector('input[type="file"]'), {
            target: { files: [file] },
          });
        } else {
          fireEvent.drop(screen.getByPlaceholderText("Ask anything..."), {
            dataTransfer: { files: [file] },
          });
        }
      };
      select();
      expect(await screen.findByRole("alert")).toHaveTextContent(detail);
      expect(useFalconStore.getState().attachedFiles).toEqual([]);
      expect(screen.getByTitle("Attach file")).not.toBeDisabled();

      vi.mocked(uploadFile).mockResolvedValueOnce({
        status: true,
        result: { id: "synthetic-file", name: file.name, size: file.size },
      });
      select();
      await waitFor(() =>
        expect(useFalconStore.getState().attachedFiles).toHaveLength(1),
      );
      expect(uploadFile).toHaveBeenCalledTimes(2);
    },
  );

  it("shows a fallback message when the upload has no server response", async () => {
    vi.mocked(uploadFile).mockRejectedValueOnce(new Error("Network Error"));
    const { container } = render(
      <SnackbarProvider>
        <ChatInput onSend={vi.fn()} />
      </SnackbarProvider>,
    );
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: {
        files: [new File(["notes"], "notes.txt", { type: "text/plain" })],
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "File upload failed. Please try again.",
    );
    expect(useFalconStore.getState().attachedFiles).toEqual([]);
  });

  it("renders the text input", () => {
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Ask anything...");
    expect(input).toBeInTheDocument();
  });

  it("shows follow-up placeholder when conversation is active", () => {
    useFalconStore.getState().setCurrentConversation("conv-1");
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Ask a follow-up...");
    expect(input).toBeInTheDocument();
  });

  it("calls onSend with trimmed text on enter key", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Ask anything...");

    fireEvent.change(input, { target: { value: "  Hello world  " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(onSend).toHaveBeenCalledWith("Hello world");
  });

  it("does not call onSend for empty/whitespace-only input", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Ask anything...");

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send on Shift+Enter (allows newline)", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Ask anything...");

    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables input when streaming", () => {
    useFalconStore.getState().setStreaming(true, "msg1");
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Ask anything...");
    expect(input).toBeDisabled();
  });

  it("shows stop button when streaming", () => {
    useFalconStore.getState().setStreaming(true, "msg1");
    render(<ChatInput onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.getByTitle("Stop")).toBeInTheDocument();
  });

  it("shows send button when not streaming", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(screen.getByTitle("Send")).toBeInTheDocument();
    expect(screen.queryByTitle("Stop")).not.toBeInTheDocument();
  });

  it("calls onStop when stop button is clicked", () => {
    useFalconStore.getState().setStreaming(true, "msg1");
    const onStop = vi.fn();
    render(<ChatInput onSend={vi.fn()} onStop={onStop} />);
    fireEvent.click(screen.getByTitle("Stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("clears input after sending", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Ask anything...");

    fireEvent.change(input, { target: { value: "Test message" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(input.value).toBe("");
  });

  it("renders utility buttons (context, attach, more)", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(screen.getByTestId("context-selector")).toBeInTheDocument();
    expect(screen.getByTitle("Attach file")).toBeInTheDocument();
    expect(screen.getByTitle("More")).toBeInTheDocument();
  });

  it("renders disclaimer text", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(
      screen.getByText("AI can make mistakes. Check important info."),
    ).toBeInTheDocument();
  });

  it("renders attached file chips when files are attached", () => {
    useFalconStore.getState().addAttachedFile({
      id: "f1",
      name: "test.csv",
      size: 1024,
      content_type: "text/csv",
    });
    render(<ChatInput onSend={vi.fn()} />);
    expect(screen.getByTestId("attached-file-chip")).toBeInTheDocument();
    expect(screen.getByText("test.csv")).toBeInTheDocument();
  });
});
