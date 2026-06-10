import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
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
});

describe("ChatInput", () => {
  it("renders the text input", () => {
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
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
    const input = screen.getByPlaceholderText("Message Falcon AI...");

    fireEvent.change(input, { target: { value: "  Hello world  " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(onSend).toHaveBeenCalledWith("Hello world");
  });

  it("does not call onSend for empty/whitespace-only input", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send on Shift+Enter (allows newline)", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");

    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables input when streaming", () => {
    useFalconStore.getState().setStreaming(true, "msg1");
    render(<ChatInput onSend={vi.fn()} />);
    // While streaming the placeholder switches to the responding hint
    const input = screen.getByPlaceholderText("Falcon is responding…");
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
    const input = screen.getByPlaceholderText("Message Falcon AI...");

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
      screen.getByText("Falcon AI can make mistakes. Check important info."),
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

describe("ChatInput message-length visibility (no silent truncation)", () => {
  // The old behavior silently sliced sent text at 10,000 chars — the model
  // answered a different question than the user believed they asked.
  it("shows a character counter from 90% of the limit", () => {
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "a".repeat(9100) } });
    expect(screen.getByText("9,100 / 10,000")).toBeInTheDocument();
  });

  it("shows no counter for short messages", () => {
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "short message" } });
    expect(screen.queryByText(/\/ 10,000/)).not.toBeInTheDocument();
  });

  it("blocks send and explains when over the 10,000-character limit", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "a".repeat(10001) } });
    expect(
      screen.getByText(/Message too long — 10,001 \/ 10,000 characters/),
    ).toBeInTheDocument();
    expect(screen.getByTitle("Send")).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends untruncated text at exactly the limit", () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    const msg = "a".repeat(10000);
    fireEvent.change(input, { target: { value: msg } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith(msg);
  });
});

describe("ChatInput oversized-file visibility (no silent drop)", () => {
  it("shows an inline notice for a >10MB file and does not upload it", async () => {
    const { uploadFile } = await import("../hooks/useFalconAPI");
    vi.mocked(uploadFile).mockClear();
    render(<ChatInput onSend={vi.fn()} />);
    const fileInput = document.querySelector('input[type="file"]');
    const bigFile = new File(["x"], "big-export.csv", { type: "text/csv" });
    Object.defineProperty(bigFile, "size", { value: 11 * 1024 * 1024 });
    fireEvent.change(fileInput, { target: { files: [bigFile] } });
    expect(
      screen.getByText(
        '"big-export.csv" is larger than 10 MB and was not attached.',
      ),
    ).toBeInTheDocument();
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("the notice is dismissable", () => {
    render(<ChatInput onSend={vi.fn()} />);
    const fileInput = document.querySelector('input[type="file"]');
    const bigFile = new File(["x"], "big.pdf", { type: "application/pdf" });
    Object.defineProperty(bigFile, "size", { value: 11 * 1024 * 1024 });
    fireEvent.change(fileInput, { target: { files: [bigFile] } });
    fireEvent.click(screen.getByTitle("Dismiss notice"));
    expect(screen.queryByText(/larger than 10 MB/)).not.toBeInTheDocument();
  });

  it("on drop, uploads valid files and reports the oversized ones", async () => {
    const { uploadFile } = await import("../hooks/useFalconAPI");
    vi.mocked(uploadFile).mockClear();
    vi.mocked(uploadFile).mockResolvedValue({
      status: true,
      result: { id: "f-ok", name: "ok.csv" },
    });
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    const okFile = new File(["x"], "ok.csv", { type: "text/csv" });
    const bigFile = new File(["x"], "huge.csv", { type: "text/csv" });
    Object.defineProperty(bigFile, "size", { value: 11 * 1024 * 1024 });
    fireEvent.drop(input, { dataTransfer: { files: [okFile, bigFile] } });
    expect(
      await screen.findByText(
        '"huge.csv" is larger than 10 MB and was not attached.',
      ),
    ).toBeInTheDocument();
    expect(uploadFile).toHaveBeenCalledTimes(1);
    expect(uploadFile).toHaveBeenCalledWith(okFile);
  });
});
