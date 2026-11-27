import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import UploadDropZone from "../UploadDropZone";

const mockUpload = vi.fn();
const mockStartScan = vi.fn();

vi.mock("src/api/agents/agentConnect", () => ({
  useStartScan: () => ({ mutate: mockStartScan, isPending: false }),
}));

vi.mock("src/pages/dashboard/harness/sourceUpload", async (importOriginal) => ({
  ...(await importOriginal()),
  uploadSourceFolder: (...args) => mockUpload(...args),
  uploadErrorText: () => "The upload failed",
}));

const auth = vi.hoisted(() => ({ role: "Member" }));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: auth.role }),
}));

const picked = (path) =>
  new File(["x"], path.split("/").at(-1), { type: "text/plain" });
const withPath = (path) => {
  const file = picked(path);
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
};
const folderInput = (container) =>
  container.querySelector('input[type="file"][webkitdirectory]');

describe("UploadDropZone", () => {
  beforeEach(() => {
    auth.role = "Member";
    mockUpload.mockReset();
    mockStartScan.mockReset();
  });

  it("asks for a folder on the page itself", () => {
    render(<UploadDropZone onScanned={vi.fn()} />);

    expect(
      screen.getByText("Choose a folder or drag & drop it here"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Browse folder" })).toBeEnabled();
  });

  it("uploads a picked folder, reads it, and hands over the scan", async () => {
    mockUpload.mockResolvedValue({ source_id: "src-1" });
    mockStartScan.mockImplementation((_source, { onSuccess }) =>
      onSuccess({ id: "scan-1" }),
    );
    const onScanned = vi.fn();
    const { container } = render(<UploadDropZone onScanned={onScanned} />);

    fireEvent.change(folderInput(container), {
      target: { files: [withPath("agent/main.py")] },
    });

    await waitFor(() => expect(onScanned).toHaveBeenCalled());
    expect(mockStartScan.mock.calls[0][0]).toEqual({
      kind: "archive",
      archive_artifact_id: "src-1",
    });
    expect(onScanned).toHaveBeenCalledWith({
      folder: { name: "agent", files: 1, bytes: 1, sourceId: "src-1" },
      scanId: "scan-1",
    });
  });

  it("shows the folder while it uploads", async () => {
    mockUpload.mockReturnValue(new Promise(() => {}));
    const { container } = render(<UploadDropZone onScanned={vi.fn()} />);

    fireEvent.change(folderInput(container), {
      target: { files: [withPath("agent/main.py")] },
    });

    expect(await screen.findByText("Uploading 0%")).toBeInTheDocument();
    expect(screen.getByText("agent")).toBeInTheDocument();
  });

  it("says why an upload failed and keeps the drop zone", async () => {
    mockUpload.mockRejectedValue(new Error("boom"));
    const onScanned = vi.fn();
    const { container } = render(<UploadDropZone onScanned={onScanned} />);

    fireEvent.change(folderInput(container), {
      target: { files: [withPath("agent/main.py")] },
    });

    expect(await screen.findByText("The upload failed")).toBeInTheDocument();
    expect(
      screen.getByText("Choose a folder or drag & drop it here"),
    ).toBeInTheDocument();
    expect(onScanned).not.toHaveBeenCalled();
  });

  it("won't let a viewer upload", () => {
    auth.role = "Viewer";
    render(<UploadDropZone onScanned={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: "Browse folder" }),
    ).toBeDisabled();
  });

  it("drops its folder picker while the drawer is open", () => {
    const { container } = render(
      <UploadDropZone onScanned={vi.fn()} disabled />,
    );

    expect(folderInput(container)).toBeNull();
  });
});
