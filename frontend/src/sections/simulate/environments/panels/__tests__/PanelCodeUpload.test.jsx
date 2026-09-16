import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render as baseRender,
  screen,
  fireEvent,
  waitFor,
} from "src/utils/test-utils";
import { uploadHarnessSource } from "src/api/harness/harness";
import PanelCodeUpload from "../PanelCodeUpload";

vi.mock("src/api/harness/harness", () => ({
  uploadHarnessSource: vi.fn(),
}));

const render = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return baseRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

const chooseFiles = (container, files) => {
  const input = container.querySelector('input[type="file"][multiple]');
  fireEvent.change(input, { target: { files } });
  return input;
};

const cta = () => screen.getByRole("button", { name: /Build environment/ });

beforeEach(() => {
  uploadHarnessSource.mockReset();
  uploadHarnessSource.mockResolvedValue({ source_id: "src-uuid-1", file_count: 2, total_bytes: 1537, name: "agent.py" });
});

describe("PanelCodeUpload", () => {
  it("shows the folder-upload copy and never mentions zip", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    expect(screen.getByText("Upload your agent folder")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/zip/i);
  });

  it("is a folder picker (webkitdirectory) like the product, with no type filter", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    const input = container.querySelector('input[type="file"][multiple]');
    expect(input.hasAttribute("webkitdirectory")).toBe(true);
    expect(input.hasAttribute("directory")).toBe(true);
    expect(input.hasAttribute("accept")).toBe(false);
  });

  it("uploads the chosen folder and shows a summary (no per-file list)", async () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);
    await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalledTimes(1));
    // Summary card, not a deletable list of file rows.
    expect(await screen.findByText(/2 files/)).toBeInTheDocument();
    expect(screen.queryByText("tools.py")).toBeNull();
    // Replace (clear/replace) is the only file control.
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
  });

  it("filters excluded files client-side and reports them in the summary", async () => {
    uploadHarnessSource.mockResolvedValue({ source_id: "src-uuid-1", file_count: 1, total_bytes: 1536 });
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["SECRET=1"], ".env"),
      new File(["a".repeat(1536)], "agent.py"),
    ]);
    await waitFor(() => {
      const formData = uploadHarnessSource.mock.calls[0][0];
      expect(formData.getAll("files")).toHaveLength(1);
    });
    expect(await screen.findByText(/1 file .* 1 excluded/)).toBeInTheDocument();
  });

  it("posts the folder as multipart files + paths + name", async () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);
    await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalledTimes(1));
    const formData = uploadHarnessSource.mock.calls[0][0];
    expect(formData.getAll("files")).toHaveLength(2);
    expect(formData.getAll("paths")).toEqual(["agent.py", "tools.py"]);
    expect(formData.get("name")).toBe("agent.py");
  });

  it("gates the CTA until the archive upload resolves, then hands the id to onBuild", async () => {
    let resolveUpload;
    uploadHarnessSource.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );
    const onBuild = vi.fn();
    const { container } = render(<PanelCodeUpload onBuild={onBuild} />);

    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    // Upload in flight: progress surface shows, but CTA still gated.
    expect(screen.getAllByText(/Uploading your folder to the runner/).length).toBeGreaterThan(0);
    expect(cta()).toBeDisabled();

    resolveUpload({ source_id: "src-uuid-1", file_count: 1, total_bytes: 1536 });
    await waitFor(() => expect(cta()).toBeEnabled());

    fireEvent.click(cta());
    expect(onBuild).toHaveBeenCalledWith({
      kind: "upload",
      entry: "agent.py",
      files: [{ name: "agent.py" }],
      archive_artifact_id: "src-uuid-1",
      envText: null,
      egress: null,
      secretFiles: [],
    });
  });

  it("renders an error and keeps the CTA gated when the upload fails", async () => {
    uploadHarnessSource.mockRejectedValue({
      response: { data: { detail: "Upload rejected" } },
    });
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);

    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    expect(await screen.findByText("Upload rejected")).toBeInTheDocument();
    expect(cta()).toBeDisabled();
  });

  it("names the field-limit cause on a 400 instead of a generic error", async () => {
    uploadHarnessSource.mockRejectedValue({ response: { status: 400 } });
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    expect(
      await screen.findByText(/too many files for the runner/i),
    ).toBeInTheDocument();
    expect(cta()).toBeDisabled();
  });

  it("surfaces a prepare error and never calls the upload endpoint", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [new File(["SECRET=1"], ".env")]);

    expect(
      screen.getByText("The selected folder contains no uploadable source files."),
    ).toBeInTheDocument();
    expect(uploadHarnessSource).not.toHaveBeenCalled();
    expect(cta()).toBeDisabled();
  });

  it("surfaces the over-cap message without uploading", () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(
      container,
      Array.from({ length: 5001 }, (_, i) => new File(["x"], `f${i}.py`)),
    );

    expect(
      screen.getByText("Source uploads support at most 5000 files and 200 MiB."),
    ).toBeInTheDocument();
    expect(uploadHarnessSource).not.toHaveBeenCalled();
    expect(cta()).toBeDisabled();
  });

  it("auto-fills the entry field from the first file in the folder", async () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);
    expect(screen.getByDisplayValue("agent.py")).toBeInTheDocument();
    await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalled());
  });

  it("replaces the whole selection on a new folder pick (all-or-nothing archive)", async () => {
    const { container } = render(<PanelCodeUpload onBuild={vi.fn()} />);
    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    await waitFor(() => expect(cta()).toBeEnabled());

    // Replace with a different folder → a fresh upload, CTA re-gated then re-enabled.
    let resolveSecond;
    uploadHarnessSource.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSecond = resolve;
      }),
    );
    chooseFiles(container, [new File(["b".repeat(2048)], "main.py")]);
    expect(cta()).toBeDisabled();
    expect(uploadHarnessSource).toHaveBeenCalledTimes(2);

    resolveSecond({ source_id: "src-uuid-2", file_count: 1, total_bytes: 2048 });
    await waitFor(() => expect(cta()).toBeEnabled());
  });
});
