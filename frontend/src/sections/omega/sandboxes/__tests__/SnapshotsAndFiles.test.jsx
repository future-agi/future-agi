import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FilesTab from "../detail/FilesTab";
import SnapshotsTab from "../detail/SnapshotsTab";
import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const SNAPSHOT = {
  id: "snap_0123456789abcdef01234567",
  sandboxId: "sbx_0123456789abcdef01234567",
  createdAt: new Date(Date.now() - 300_000).toISOString(),
  kind: "files",
  statement: "Files only; memory and running services are not saved.",
  contentAddress: { artifactId: "art_1", sha256: "a".repeat(64) },
};

describe("Snapshots tab", () => {
  it("labels snapshots with the API's files-only statement and restores one as a new sandbox", async () => {
    const api = fakeApi({ snapshots: vi.fn(async () => [SNAPSHOT]) });
    renderSandboxes(<SnapshotsTab sandbox={sandboxView()} />, { api });
    expect(screen.getByTestId("snapshot-statement")).toHaveTextContent(
      "Files only; memory and running services are not saved.",
    );
    fireEvent.click(await screen.findByTestId("snapshot-restore"));
    fireEvent.change(screen.getByTestId("restore-name"), {
      target: { value: "copy" },
    });
    fireEvent.click(screen.getByTestId("restore-confirm"));
    await waitFor(() =>
      expect(api.restore).toHaveBeenCalledWith(
        "sbx_0123456789abcdef01234567",
        SNAPSHOT.id,
        { name: "copy" },
        { idempotencyKey: expect.stringMatching(/^fa-sbx-/) },
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        "/dashboard/sandboxes/sbx_restored00000000000000000",
      ),
    );
  });

  it("takes a snapshot only while the sandbox is running", async () => {
    const api = fakeApi();
    const { unmount } = renderSandboxes(
      <SnapshotsTab sandbox={sandboxView()} />,
      { api },
    );
    fireEvent.click(screen.getByTestId("snapshot-take"));
    await waitFor(() =>
      expect(api.snapshot).toHaveBeenCalledWith(
        "sbx_0123456789abcdef01234567",
        {
          idempotencyKey: expect.stringMatching(/^fa-sbx-/),
        },
      ),
    );
    unmount();
    renderSandboxes(
      <SnapshotsTab sandbox={sandboxView({ state: "hibernated" })} />,
      { api },
    );
    expect(screen.getByTestId("snapshot-take")).toBeDisabled();
  });
});

describe("Files tab", () => {
  it("browses, reads and saves a file through the audited write route", async () => {
    let content = "first\n";
    const api = fakeApi({
      listFiles: vi.fn(async () => ({
        path: "",
        entries: [
          { name: "notes.txt", size: 6, mode: "-rw-r--r--" },
          { name: "src", size: 64, mode: "drwxr-xr-x", directory: true },
        ],
      })),
      readFile: vi.fn(async () => ({
        path: "notes.txt",
        encoding: "utf8",
        content,
        bytes: content.length,
        truncated: false,
        modifiedAt: new Date().toISOString(),
      })),
      writeFile: vi.fn(async (_id, path, next) => {
        content = next;
        return {
          path,
          bytes: next.length,
          modifiedAt: new Date().toISOString(),
        };
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    const entries = await screen.findAllByTestId("file-entry");
    expect(entries.map((e) => e.dataset.name)).toEqual(["notes.txt", "src"]);
    fireEvent.click(entries[0]);
    expect(await screen.findByTestId("file-content")).toHaveTextContent(
      "first",
    );
    fireEvent.click(screen.getByTestId("file-edit"));
    fireEvent.change(screen.getByTestId("file-editor"), {
      target: { value: "second\n" },
    });
    fireEvent.click(screen.getByTestId("file-save"));
    await waitFor(() =>
      expect(api.writeFile).toHaveBeenCalledWith(
        "sbx_0123456789abcdef01234567",
        "notes.txt",
        "second\n",
      ),
    );
    expect(await screen.findByTestId("file-saved")).toHaveTextContent(
      "recorded in Activity",
    );
    await waitFor(() =>
      expect(screen.getByTestId("file-content")).toHaveTextContent("second"),
    );
    fireEvent.click(screen.getAllByTestId("file-entry")[1]);
    await waitFor(() =>
      expect(api.listFiles).toHaveBeenLastCalledWith(
        "sbx_0123456789abcdef01234567",
        "src",
      ),
    );
  });

  it("does not read files of a hibernated sandbox", () => {
    const api = fakeApi();
    renderSandboxes(
      <FilesTab sandbox={sandboxView({ state: "hibernated" })} />,
      { api },
    );
    expect(
      screen.getByText("Files are kept while the sandbox is hibernated"),
    ).toBeInTheDocument();
    expect(api.listFiles).not.toHaveBeenCalled();
  });
});
