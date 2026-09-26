import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render as baseRender,
  screen,
  fireEvent,
  waitFor,
} from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// uploadHarnessSource is the archive upload; preflightHarnessJob is the inline
// preflight. The rest of the surface is imported by the environments tree.
vi.mock("src/api/harness/harness", () => ({
  uploadHarnessSource: vi.fn(),
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

const { uploadHarnessSource, preflightHarnessJob, createHarnessJob } = await import(
  "src/api/harness/harness"
);
const { default: PanelCodeUpload } = await import("../PanelCodeUpload");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);

const PASS = {
  ready_to_submit: true,
  credentials: {
    scanned_files: 1,
    detected_connectors: [],
    requirements: [],
    credential_choices: [],
    probe: [],
  },
};

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

const preflightBtn = () => screen.getByRole("button", { name: "Run preflight" });
const buildBtn = () => screen.getByRole("button", { name: /Build environment/ });

beforeEach(() => {
  resetEnvironmentsStore();
  navigate.mockReset();
  uploadHarnessSource.mockReset();
  preflightHarnessJob.mockReset();
  uploadHarnessSource.mockResolvedValue({ source_id: "src-uuid-1", file_count: 2, total_bytes: 1537, name: "agent.py" });
});

describe("PanelCodeUpload", () => {
  it("shows the folder-upload copy and never mentions zip", () => {
    const { container } = render(<PanelCodeUpload />);
    expect(screen.getByText("Upload your agent folder")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/zip/i);
  });

  it("is a folder picker (webkitdirectory) like the product, with no type filter", () => {
    const { container } = render(<PanelCodeUpload />);
    const input = container.querySelector('input[type="file"][multiple]');
    expect(input.hasAttribute("webkitdirectory")).toBe(true);
    expect(input.hasAttribute("directory")).toBe(true);
    expect(input.hasAttribute("accept")).toBe(false);
  });

  it("uploads the chosen folder and shows a summary (no per-file list)", async () => {
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);
    await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/2 files/)).toBeInTheDocument();
    expect(screen.queryByText("tools.py")).toBeNull();
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
  });

  it("filters excluded files client-side and reports them in the summary", async () => {
    uploadHarnessSource.mockResolvedValue({ source_id: "src-uuid-1", file_count: 1, total_bytes: 1536 });
    const { container } = render(<PanelCodeUpload />);
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
    const { container } = render(<PanelCodeUpload />);
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

  it("gates Run preflight until the archive resolves, then builds through preflight", async () => {
    let resolveUpload;
    uploadHarnessSource.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );
    preflightHarnessJob.mockResolvedValue(PASS);
    const { container } = render(<PanelCodeUpload />);

    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    // Upload in flight: progress shows, but both actions are gated.
    expect(screen.getAllByText(/Uploading your folder to the runner/).length).toBeGreaterThan(0);
    expect(preflightBtn()).toBeDisabled();
    expect(buildBtn()).toBeDisabled();

    resolveUpload({ source_id: "src-uuid-1", file_count: 1, total_bytes: 1536 });
    // Upload resolved → Run preflight enables; Build stays gated until it passes.
    await waitFor(() => expect(preflightBtn()).toBeEnabled());
    expect(buildBtn()).toBeDisabled();

    fireEvent.click(preflightBtn());
    await waitFor(() => expect(buildBtn()).toBeEnabled());
    // The preflight body points at the uploaded archive.
    expect(preflightHarnessJob).toHaveBeenCalledWith(
      expect.objectContaining({
        source: expect.objectContaining({ kind: "archive", archive_artifact_id: "src-uuid-1" }),
      }),
    );

    createHarnessJob.mockResolvedValue({ job: { job_id: "job-up" } });
    fireEvent.click(buildBtn());
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/environments/job-up"),
    );
    // The passing draft is kept in the persisted slot for form rehydrate.
    expect(useEnvironmentsStore.getState().draft).toMatchObject({
      kind: "upload",
      entry: "agent.py",
      archive_artifact_id: "src-uuid-1",
    });
  });

  it("renders an error and keeps Run preflight gated when the upload fails", async () => {
    uploadHarnessSource.mockRejectedValue({
      response: { data: { detail: "Upload rejected" } },
    });
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    expect(await screen.findByText("Upload rejected")).toBeInTheDocument();
    expect(preflightBtn()).toBeDisabled();
  });

  it("names the field-limit cause on a 400 instead of a generic error", async () => {
    uploadHarnessSource.mockRejectedValue({ response: { status: 400 } });
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    expect(
      await screen.findByText(/too many files for the runner/i),
    ).toBeInTheDocument();
    expect(preflightBtn()).toBeDisabled();
  });

  it("surfaces a prepare error and never calls the upload endpoint", () => {
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(container, [new File(["SECRET=1"], ".env")]);
    expect(
      screen.getByText("The selected folder contains no uploadable source files."),
    ).toBeInTheDocument();
    expect(uploadHarnessSource).not.toHaveBeenCalled();
    expect(preflightBtn()).toBeDisabled();
  });

  it("surfaces the over-cap message without uploading", () => {
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(
      container,
      Array.from({ length: 1001 }, (_, i) => new File(["x"], `f${i}.py`)),
    );
    expect(
      screen.getByText(/this folder has 1001 files; uploads support at most 1000/i),
    ).toBeInTheDocument();
    expect(uploadHarnessSource).not.toHaveBeenCalled();
    expect(preflightBtn()).toBeDisabled();
  });

  it("auto-fills the entry field from the first file in the folder", async () => {
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(container, [
      new File(["a".repeat(1536)], "agent.py"),
      new File(["x"], "tools.py"),
    ]);
    expect(screen.getByDisplayValue("agent.py")).toBeInTheDocument();
    await waitFor(() => expect(uploadHarnessSource).toHaveBeenCalled());
  });

  it("replaces the whole selection on a new folder pick (all-or-nothing archive)", async () => {
    const { container } = render(<PanelCodeUpload />);
    chooseFiles(container, [new File(["a".repeat(1536)], "agent.py")]);
    await waitFor(() => expect(preflightBtn()).toBeEnabled());

    // Replace with a different folder → a fresh upload, Run preflight re-gated then
    // re-enabled.
    let resolveSecond;
    uploadHarnessSource.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSecond = resolve;
      }),
    );
    chooseFiles(container, [new File(["b".repeat(2048)], "main.py")]);
    expect(preflightBtn()).toBeDisabled();
    expect(uploadHarnessSource).toHaveBeenCalledTimes(2);

    resolveSecond({ source_id: "src-uuid-2", file_count: 1, total_bytes: 2048 });
    await waitFor(() => expect(preflightBtn()).toBeEnabled());
  });
});
