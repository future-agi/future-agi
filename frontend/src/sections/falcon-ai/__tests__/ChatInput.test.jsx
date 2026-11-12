import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  act,
  within,
  waitFor,
} from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import { uploadFile } from "../hooks/useFalconAPI";
import ChatInput from "../components/ChatInput";

const { notify, push } = vi.hoisted(() => ({
  notify: vi.fn(),
  push: vi.fn(),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: notify,
}));

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
  useRouter: () => ({ push, replace: vi.fn() }),
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
  notify.mockReset();
  push.mockReset();
  uploadFile.mockReset();
});

// A file the browser reports as 11 MB, without allocating 11 MB.
function oversizedFile(name) {
  const file = new File(["x"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: 11 * 1024 * 1024 });
  return file;
}

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
    const input = screen.getByPlaceholderText("Message Falcon AI...");
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

describe("ChatInput connectors menu (+ > Connectors)", () => {
  it("shows each connector's status as text, beside its on/off switch turned off", async () => {
    useFalconStore.getState().setConnectors([
      { id: "c1", name: "Notion", is_active: true, is_verified: true },
      { id: "c2", name: "Linear", is_active: true, is_verified: false },
      { id: "c3", name: "Jira", is_active: false, is_verified: true },
    ]);
    render(<ChatInput onSend={vi.fn()} />);

    fireEvent.click(screen.getByTitle("More"));
    fireEvent.mouseEnter(screen.getByText("Connectors"));

    const row = async (name) => (await screen.findByText(name)).closest("li");
    expect(within(await row("Notion")).getByText("Connected")).toBeTruthy();
    expect(within(await row("Linear")).getByText("Pending")).toBeTruthy();
    expect(within(await row("Jira")).getByText("Inactive")).toBeTruthy();

    // The setting is shared by the whole workspace, so the chat box shows it
    // on a switch that is turned off and says why.
    const toggle = async (name) =>
      within(await row(name)).getByRole("checkbox", {
        name: `Turn ${name} on or off`,
      });
    for (const name of ["Notion", "Linear", "Jira"]) {
      const sw = await toggle(name);
      expect(sw).toBeDisabled();
      expect(sw).toHaveAccessibleDescription("Not available in this release");
    }
    expect(await toggle("Notion")).toBeChecked();
    expect(await toggle("Linear")).toBeChecked();
    expect(await toggle("Jira")).not.toBeChecked();
  });

  it("opens the connectors settings page when a connector is clicked", async () => {
    useFalconStore
      .getState()
      .setConnectors([
        { id: "c2", name: "Linear", is_active: true, is_verified: false },
      ]);
    render(<ChatInput onSend={vi.fn()} />);

    fireEvent.click(screen.getByTitle("More"));
    fireEvent.mouseEnter(screen.getByText("Connectors"));
    fireEvent.click((await screen.findByText("Linear")).closest("li"));

    expect(push).toHaveBeenCalledTimes(1);
    expect(push).toHaveBeenCalledWith(
      "/dashboard/settings/falcon-ai-connectors",
    );
  });

  it("opens the connectors settings page when Enter is pressed on a connector", async () => {
    useFalconStore
      .getState()
      .setConnectors([
        { id: "c2", name: "Linear", is_active: true, is_verified: false },
      ]);
    render(<ChatInput onSend={vi.fn()} />);

    fireEvent.click(screen.getByTitle("More"));
    fireEvent.mouseEnter(screen.getByText("Connectors"));
    fireEvent.keyDown((await screen.findByText("Linear")).closest("li"), {
      key: "Enter",
    });

    expect(push).toHaveBeenCalledTimes(1);
    expect(push).toHaveBeenCalledWith(
      "/dashboard/settings/falcon-ai-connectors",
    );
  });

  // Opening settings leaves the Chat page, and the message being typed with
  // it, so the turned-off switch must not do what a click on the row does.
  it("does nothing when a connector's turned-off switch is clicked", async () => {
    useFalconStore
      .getState()
      .setConnectors([
        { id: "c2", name: "Linear", is_active: true, is_verified: false },
      ]);
    render(<ChatInput onSend={vi.fn()} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "A long draft" } });

    fireEvent.click(screen.getByTitle("More"));
    fireEvent.mouseEnter(screen.getByText("Connectors"));
    const toggle = await screen.findByRole("checkbox", {
      name: "Turn Linear on or off",
    });
    // The turned-off switch takes no pointer events itself, so in a browser
    // the click lands on the switch's track around it.
    fireEvent.click(toggle.closest(".MuiSwitch-root"));

    expect(push).not.toHaveBeenCalled();
    expect(toggle).toBeChecked();
    expect(screen.getByText("Linear")).toBeInTheDocument();
    expect(input).toHaveValue("A long draft");
  });
});

describe("ChatInput file size limit (10 MB)", () => {
  it("says which picked file was not attached, and why", () => {
    const { container } = render(<ChatInput onSend={vi.fn()} />);

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [oversizedFile("report.pdf")] },
    });

    expect(uploadFile).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledTimes(1);
    const [message, options] = notify.mock.calls[0];
    expect(message).toContain("report.pdf");
    expect(message).toContain("10 MB");
    expect(options).toMatchObject({ variant: "error" });
  });

  it("uploads dropped files that fit and names the one that was too big", async () => {
    uploadFile.mockResolvedValue({
      status: true,
      result: { id: "f1", name: "notes.txt", size: 4 },
    });
    render(<ChatInput onSend={vi.fn()} />);
    const small = new File(["data"], "notes.txt", { type: "text/plain" });

    await act(async () => {
      fireEvent.drop(screen.getByPlaceholderText("Message Falcon AI..."), {
        dataTransfer: { files: [oversizedFile("video.mp4"), small] },
      });
    });

    expect(uploadFile).toHaveBeenCalledTimes(1);
    expect(uploadFile).toHaveBeenCalledWith(small);
    expect(notify).toHaveBeenCalledTimes(1);
    const [message] = notify.mock.calls[0];
    expect(message).toContain("video.mp4");
    expect(message).toContain("10 MB");
    expect(message).not.toContain("notes.txt");
  });

  it("does not warn when every dropped file fits", async () => {
    uploadFile.mockResolvedValue({
      status: true,
      result: { id: "f1", name: "notes.txt", size: 4 },
    });
    render(<ChatInput onSend={vi.fn()} />);

    await act(async () => {
      fireEvent.drop(screen.getByPlaceholderText("Message Falcon AI..."), {
        dataTransfer: {
          files: [new File(["data"], "notes.txt", { type: "text/plain" })],
        },
      });
    });

    expect(uploadFile).toHaveBeenCalledTimes(1);
    expect(notify).not.toHaveBeenCalled();
  });
});

// A small file with the given name.
function smallFile(name, type = "text/plain") {
  return new File(["data"], name, { type });
}

// What the server returns once a file is stored.
function stored(file) {
  return {
    status: true,
    result: { id: file.name, name: file.name, size: file.size },
  };
}

function dropFiles(files) {
  return act(async () => {
    fireEvent.drop(screen.getByPlaceholderText("Message Falcon AI..."), {
      dataTransfer: { files },
    });
  });
}

// The shared axios client rejects with the server's error body, so a refused
// upload arrives as a plain object whose message is the server's reason.
describe("ChatInput says when a file wasn't attached", () => {
  it("names a picked file the server refused, with the server's reason", async () => {
    uploadFile.mockRejectedValue({
      status: false,
      statusCode: 400,
      message: "File type not allowed",
    });
    const { container } = render(<ChatInput onSend={vi.fn()} />);

    await act(async () => {
      fireEvent.change(container.querySelector('input[type="file"]'), {
        target: { files: [smallFile("deck.pptx")] },
      });
    });

    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
    const [message, options] = notify.mock.calls[0];
    expect(message).toContain("deck.pptx");
    expect(message).toContain("File type not allowed");
    expect(options).toMatchObject({ variant: "error" });
    expect(screen.queryByTestId("attached-file-chip")).toBeNull();
  });

  it("names each dropped file that didn't upload, with its reason, and keeps the ones that did", async () => {
    uploadFile.mockImplementation((file) => {
      if (file.name === "a.txt") {
        return Promise.reject({ message: "Network Error" });
      }
      if (file.name === "b.csv") {
        return Promise.resolve({
          status: false,
          message: "No organization context",
        });
      }
      return Promise.resolve(stored(file));
    });
    render(<ChatInput onSend={vi.fn()} />);

    await dropFiles([
      smallFile("a.txt"),
      smallFile("b.csv", "text/csv"),
      smallFile("c.md", "text/markdown"),
    ]);

    await waitFor(() => expect(notify).toHaveBeenCalledTimes(2));
    const messages = notify.mock.calls.map(([message]) => message);
    expect(messages.find((m) => m.includes("a.txt"))).toContain(
      "Network Error",
    );
    expect(messages.find((m) => m.includes("b.csv"))).toContain(
      "No organization context",
    );
    expect(messages.join(" ")).not.toContain("c.md");
    expect(
      screen.getAllByTestId("attached-file-chip").map((c) => c.textContent),
    ).toEqual(["c.md"]);
  });

  it("names dropped files that failed for the same reason in one notice", async () => {
    uploadFile.mockImplementation(() =>
      Promise.reject({ message: "Network Error" }),
    );
    render(<ChatInput onSend={vi.fn()} />);

    await dropFiles([smallFile("a.txt"), smallFile("b.txt")]);

    await waitFor(() => expect(notify).toHaveBeenCalledTimes(1));
    const [message, options] = notify.mock.calls[0];
    expect(message).toContain("a.txt, b.txt weren't attached");
    expect(message).toContain("Network Error");
    expect(options).toMatchObject({ variant: "error" });
  });

  it("doesn't upload a dropped file of a type Falcon can't read, and names it", async () => {
    uploadFile.mockImplementation((file) => Promise.resolve(stored(file)));
    render(<ChatInput onSend={vi.fn()} />);
    const deck = smallFile(
      "deck.pptx",
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    );
    const photo = smallFile("Photo.PNG", "image/png");

    await dropFiles([deck, photo]);

    expect(uploadFile).toHaveBeenCalledTimes(1);
    expect(uploadFile).toHaveBeenCalledWith(photo);
    expect(notify).toHaveBeenCalledTimes(1);
    const [message, options] = notify.mock.calls[0];
    expect(message).toContain("deck.pptx wasn't attached");
    // The notice lists the types that can be attached.
    expect(message).toContain(".docx");
    expect(message).not.toContain("Photo.PNG");
    expect(options).toMatchObject({ variant: "error" });
  });

  it("says which dropped files beyond the first five weren't attached", async () => {
    uploadFile.mockImplementation((file) => Promise.resolve(stored(file)));
    render(<ChatInput onSend={vi.fn()} />);
    const names = ["a", "b", "c", "d", "e", "f", "g"].map((n) => `${n}.txt`);

    await dropFiles(names.map((name) => smallFile(name)));

    expect(uploadFile).toHaveBeenCalledTimes(5);
    expect(notify).toHaveBeenCalledTimes(1);
    const [message, options] = notify.mock.calls[0];
    expect(message).toContain("f.txt, g.txt weren't attached");
    expect(message).toContain("5 files");
    expect(message).not.toContain("a.txt");
    expect(options).toMatchObject({ variant: "error" });
    await waitFor(() =>
      expect(screen.getAllByTestId("attached-file-chip")).toHaveLength(5),
    );
  });

  it("shows Uploading... while the first file uploads, before any chip exists", async () => {
    let finish;
    uploadFile.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<ChatInput onSend={vi.fn()} />);
    const notes = smallFile("notes.txt");

    await dropFiles([notes]);

    expect(screen.getByText("Uploading...")).toBeInTheDocument();
    await act(async () => finish(stored(notes)));
    expect(screen.queryByText("Uploading...")).toBeNull();
    expect(screen.getByTestId("attached-file-chip")).toHaveTextContent(
      "notes.txt",
    );
  });

  it("stays quiet about an upload that fails after the chat was cleared", async () => {
    let fail;
    uploadFile.mockReturnValue(
      new Promise((_, reject) => {
        fail = reject;
      }),
    );
    render(<ChatInput onSend={vi.fn()} />);

    await dropFiles([smallFile("notes.txt")]);
    act(() => useFalconStore.getState().resetChat());
    await act(async () => fail({ message: "Network Error" }));

    expect(notify).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("attached-file-chip")).toBeNull();
  });
});

// This box often sits in a drawer (Imagine under a trace, chat or voice
// drawer, and the compact Falcon sidebar), and a toast is gone after a few
// seconds, so the box itself says why.
describe("ChatInput says in the message box why a file wasn't attached", () => {
  it("shows the notice in the message box, not only as a toast", async () => {
    render(<ChatInput onSend={vi.fn()} />);

    await dropFiles([
      smallFile(
        "deck.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      ),
    ]);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "deck.pptx wasn't attached.",
    );
    expect(uploadFile).not.toHaveBeenCalled();
  });

  it("shows an upload the server refused, with the server's reason", async () => {
    uploadFile.mockRejectedValue({
      status: false,
      statusCode: 400,
      message: "File type not allowed",
    });
    const { container } = render(<ChatInput onSend={vi.fn()} />);

    await act(async () => {
      fireEvent.change(container.querySelector('input[type="file"]'), {
        target: { files: [smallFile("deck.pptx")] },
      });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "deck.pptx wasn't attached. File type not allowed.",
    );
  });

  it("lists every file from one drop that wasn't attached, each with its reason", async () => {
    uploadFile.mockRejectedValue({ message: "Network Error" });
    render(<ChatInput onSend={vi.fn()} />);

    await dropFiles([
      oversizedFile("video.mp4"),
      smallFile("deck.pptx"),
      smallFile("notes.txt"),
    ]);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "notes.txt wasn't attached. Network Error.",
      ),
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "video.mp4 wasn't attached. Files must be 10 MB or smaller.",
    );
    expect(alert).toHaveTextContent(
      "deck.pptx wasn't attached. Supported file types:",
    );
  });

  it("clears the notice on the next drop, and when dismissed", async () => {
    uploadFile.mockImplementation((file) => Promise.resolve(stored(file)));
    render(<ChatInput onSend={vi.fn()} />);

    await dropFiles([smallFile("deck.pptx")]);
    expect(screen.getByRole("alert")).toHaveTextContent("deck.pptx");

    await dropFiles([smallFile("notes.txt")]);
    expect(screen.queryByRole("alert")).toBeNull();

    await dropFiles([smallFile("old.zip")]);
    expect(screen.getByRole("alert")).toHaveTextContent("old.zip");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// A message sent while a file is still uploading goes without it, and in a new
// chat the upload's result is then dropped unseen, so Send waits.
describe("ChatInput waits for uploads before sending", () => {
  it("doesn't send while a file is still uploading, and keeps what was typed", async () => {
    let finish;
    uploadFile.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    const report = smallFile("report.pdf", "application/pdf");

    fireEvent.change(input, { target: { value: "Summarize this report" } });
    await dropFiles([report]);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    fireEvent.click(screen.getByTitle("Send"));

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByTitle("Send")).toBeDisabled();
    expect(input.value).toBe("Summarize this report");
    expect(screen.getByText("Uploading...")).toBeInTheDocument();

    await act(async () => finish(stored(report)));
    await screen.findByText("report.pdf");
    expect(screen.getByTitle("Send")).not.toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("Summarize this report");
  });

  it("keeps Send off until every dropped file has finished uploading", async () => {
    const finish = {};
    uploadFile.mockImplementation(
      (file) =>
        new Promise((resolve) => {
          finish[file.name] = () => resolve(stored(file));
        }),
    );
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "Compare these" } });

    await dropFiles([smallFile("a.txt")]);
    await dropFiles([smallFile("b.txt")]);
    await act(async () => finish["a.txt"]());
    // a.txt is attached; b.txt is still uploading.
    await screen.findByText("a.txt");

    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByTitle("Send")).toBeDisabled();
    expect(screen.getByText("Uploading...")).toBeInTheDocument();

    await act(async () => finish["b.txt"]());
    await screen.findByText("b.txt");
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("Compare these");
  });
});
