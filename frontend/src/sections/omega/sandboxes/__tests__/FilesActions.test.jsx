import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FilesTab from "../detail/FilesTab";
import { OmegaError } from "src/sections/omega/shared";
import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const ID = "sbx_0123456789abcdef01234567";
const XSS = "<img src=x onerror=alert(1)>";

const listing = (entries) => vi.fn(async () => ({ path: "", entries }));
const ENTRIES = [
  { name: "chart.png", size: 2048, mode: "-rw-r--r--" },
  { name: "notes.txt", size: 6, mode: "-rw-r--r--" },
  { name: "src", size: 64, mode: "drwxr-xr-x", directory: true },
];

// jsdom has no object URLs; the tab only needs a URL per blob and a revoke.
let created;
beforeEach(() => {
  created = [];
  URL.createObjectURL = vi.fn((blob) => {
    created.push(blob);
    return `blob:test/${created.length}`;
  });
  URL.revokeObjectURL = vi.fn();
});
// Kept defined after each test: an unmounting preview still revokes its URL.
afterEach(() => vi.restoreAllMocks());

async function openMenu(name) {
  const row = (await screen.findAllByTestId("file-entry"))
    .find((e) => e.dataset.name === name)
    .closest("li");
  fireEvent.click(within(row).getByTestId("file-actions"));
}

describe("Files tab actions (W2-FILES)", () => {
  it("uploads chosen files into the open folder and reports each one", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    fireEvent.click((await screen.findAllByTestId("file-entry"))[2]);
    await waitFor(() =>
      expect(api.listFiles).toHaveBeenLastCalledWith(ID, "src"),
    );
    const file = new File(["hello"], "data.csv", { type: "text/csv" });
    fireEvent.change(screen.getByTestId("file-upload-input"), {
      target: { files: [file] },
    });
    await waitFor(() =>
      expect(api.uploadFile).toHaveBeenCalledWith(ID, "src/data.csv", file),
    );
    expect(await screen.findByTestId("upload-results")).toHaveTextContent(
      "Uploaded 1 of 1 (5 B)",
    );
  });

  it("says why an upload was refused, in Future AGI's words", async () => {
    const api = fakeApi({
      listFiles: listing(ENTRIES),
      uploadFile: vi.fn(async () => {
        throw new OmegaError({
          state: "error",
          status: 413,
          code: "too_large",
          message: "server words",
        });
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findAllByTestId("file-entry");
    fireEvent.drop(screen.getByTestId("files-list"), {
      dataTransfer: { files: [new File(["x"], "big.bin")], types: ["Files"] },
    });
    expect(await screen.findByTestId("upload-results")).toHaveTextContent(
      "big.bin: This file is larger than this sandbox accepts in one upload.",
    );
  });

  it("previews an image from its bytes, without its JSON read, and downloads it", async () => {
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    fireEvent.click((await screen.findAllByTestId("file-entry"))[0]);
    const img = await screen.findByTestId("file-preview");
    expect(img.getAttribute("src")).toMatch(/^blob:test\//);
    expect(img).toHaveAttribute("alt", "Preview of chart.png");
    expect(api.downloadFile).toHaveBeenCalledWith(
      ID,
      "chart.png",
      expect.anything(),
    );
    expect(api.readFile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("file-download"));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(api.downloadFile).toHaveBeenLastCalledWith(ID, "chart.png");
  });

  it("downloads a file from its row menu under the file's own name", async () => {
    let saved;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function record() {
        saved = this.download;
      },
    );
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await openMenu("notes.txt");
    fireEvent.click(await screen.findByTestId("file-action-download"));
    await waitFor(() => expect(saved).toBe("notes.txt"));
    expect(api.downloadFile).toHaveBeenCalledWith(ID, "notes.txt");
  });

  it("renames a file, keeps it open under its new name, and refuses a name with a slash", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    fireEvent.click((await screen.findAllByTestId("file-entry"))[1]);
    await screen.findByTestId("file-viewer");
    await openMenu("notes.txt");
    fireEvent.click(await screen.findByTestId("file-action-rename"));
    const input = await screen.findByTestId("file-name-input");
    expect(input).toHaveValue("notes.txt");
    fireEvent.change(input, { target: { value: "a/b.txt" } });
    fireEvent.blur(input);
    expect(
      await screen.findByText("A name cannot contain /."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("file-name-submit")).toBeDisabled();
    fireEvent.change(input, { target: { value: "renamed.txt" } });
    fireEvent.click(screen.getByTestId("file-name-submit"));
    await waitFor(() =>
      expect(api.movePath).toHaveBeenCalledWith(ID, "notes.txt", "renamed.txt"),
    );
    await waitFor(() =>
      expect(
        within(screen.getByTestId("file-viewer")).getByText("renamed.txt"),
      ).toBeInTheDocument(),
    );
  });

  it("shows the sandbox's refusal in the rename dialog and keeps it open", async () => {
    const api = fakeApi({
      listFiles: listing(ENTRIES),
      movePath: vi.fn(async () => {
        throw new OmegaError({
          state: "error",
          status: 409,
          code: "conflict",
          message: "Something already exists at taken.txt.",
        });
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await openMenu("notes.txt");
    fireEvent.click(await screen.findByTestId("file-action-rename"));
    fireEvent.change(await screen.findByTestId("file-name-input"), {
      target: { value: "taken.txt" },
    });
    fireEvent.click(screen.getByTestId("file-name-submit"));
    expect(await screen.findByTestId("file-name-error")).toHaveTextContent(
      "Something already exists at taken.txt.",
    );
    expect(screen.getByTestId("file-name-input")).toBeInTheDocument();
  });

  it("deletes a folder and everything in it only after asking", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await openMenu("src");
    expect(
      screen.queryByTestId("file-action-download"),
    ).not.toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("file-action-delete"));
    expect(
      await screen.findByText(/The folder and everything in it are removed/),
    ).toBeInTheDocument();
    expect(api.deletePath).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("file-delete-confirm"));
    await waitFor(() =>
      expect(api.deletePath).toHaveBeenCalledWith(ID, "src", {
        recursive: true,
      }),
    );
  });

  it("creates a folder in the open folder", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findAllByTestId("file-entry");
    fireEvent.click(screen.getByTestId("new-folder"));
    fireEvent.change(await screen.findByTestId("file-name-input"), {
      target: { value: "out" },
    });
    fireEvent.click(screen.getByTestId("file-name-submit"));
    await waitFor(() => expect(api.makeFolder).toHaveBeenCalledWith(ID, "out"));
  });

  it("offers no upload, rename, delete or download on an agent run's read-only desk", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView({ readOnly: true })} />, {
      api,
    });
    await screen.findAllByTestId("file-entry");
    expect(screen.queryByTestId("file-upload")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-folder")).not.toBeInTheDocument();
    expect(screen.queryByTestId("file-actions")).not.toBeInTheDocument();
  });

  it("renders a hostile file name as text in the list, the menu and the viewer", async () => {
    const api = fakeApi({
      listFiles: listing([{ name: `${XSS}.txt`, size: 3, mode: "-rw-r--r--" }]),
      readFile: vi.fn(async () => ({
        path: `${XSS}.txt`,
        encoding: "utf8",
        content: "<script>alert(2)</script>",
        bytes: 25,
        truncated: false,
        modifiedAt: new Date().toISOString(),
      })),
    });
    const { container } = renderSandboxes(
      <FilesTab sandbox={sandboxView()} />,
      { api },
    );
    const entry = (await screen.findAllByTestId("file-entry"))[0];
    expect(entry).toHaveTextContent(`${XSS}.txt`);
    fireEvent.click(entry);
    expect(await screen.findByTestId("file-content")).toHaveTextContent(
      "<script>alert(2)</script>",
    );
    await openMenu(`${XSS}.txt`);
    fireEvent.click(await screen.findByTestId("file-action-delete"));
    expect(await screen.findByText(`Delete ${XSS}.txt?`)).toBeInTheDocument();
    expect(container.querySelector('img[src="x"]')).toBeNull();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });
});
