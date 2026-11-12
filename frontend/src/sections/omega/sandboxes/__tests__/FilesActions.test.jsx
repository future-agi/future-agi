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
    // Wait for the folder's list: until then an upload asks first.
    await screen.findAllByTestId("file-entry");
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

  it("asks before an upload replaces a file in the folder, and uploads only after Replace", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findAllByTestId("file-entry");
    const file = new File(["fresh"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("file-upload-input"), {
      target: { files: [file] },
    });
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Replace 1 file?");
    expect(dialog).toHaveTextContent(
      "notes.txt already exists in /workspace. Uploading replaces it. Snapshots already taken are not changed.",
    );
    expect(api.uploadFile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("upload-replace-confirm"));
    await waitFor(() =>
      expect(api.uploadFile).toHaveBeenCalledWith(ID, "notes.txt", file),
    );
  });

  it("uploads nothing from a drop that would replace files when Cancel is chosen", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findAllByTestId("file-entry");
    fireEvent.drop(screen.getByTestId("files-list"), {
      dataTransfer: {
        files: [
          new File(["a"], "notes.txt"),
          new File(["b"], "chart.png"),
          new File(["c"], "new.txt"),
        ],
        types: ["Files"],
      },
    });
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Replace 2 files?");
    expect(dialog).toHaveTextContent(
      "notes.txt, chart.png already exist in /workspace. Uploading replaces them. Snapshots already taken are not changed. Nothing is uploaded if you cancel.",
    );
    fireEvent.click(screen.getByTestId("upload-replace-cancel"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.uploadFile).not.toHaveBeenCalled();
  });

  it("asks before any upload while the folder's list is still loading, since nothing can be checked", async () => {
    const api = fakeApi({ listFiles: vi.fn(() => new Promise(() => {})) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    const file = new File(["fresh"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("file-upload-input"), {
      target: { files: [file] },
    });
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Upload 1 file?");
    expect(dialog).toHaveTextContent(
      "The list of /workspace hasn't loaded yet, so it can't be checked whether notes.txt is already there. Uploading replaces any file with the same name. Snapshots already taken are not changed.",
    );
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(screen.getByTestId("upload-replace-confirm")).toHaveTextContent(
      "Upload",
    );
    fireEvent.click(screen.getByTestId("upload-replace-confirm"));
    await waitFor(() =>
      expect(api.uploadFile).toHaveBeenCalledWith(ID, "notes.txt", file),
    );
  });

  it("says an upload can't be checked against a folder whose list couldn't be read", async () => {
    const api = fakeApi({
      listFiles: vi.fn(async () => {
        throw new OmegaError({
          state: "error",
          status: 500,
          code: "internal",
          message: "server words",
        });
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findByTestId("sandbox-error-state");
    fireEvent.drop(screen.getByTestId("files-list"), {
      dataTransfer: { files: [new File(["a"], "notes.txt")], types: ["Files"] },
    });
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Upload 1 file?");
    expect(dialog).toHaveTextContent(
      "The list of /workspace couldn't be read, so it can't be checked whether notes.txt is already there. Uploading replaces any file with the same name. Snapshots already taken are not changed.",
    );
    expect(api.uploadFile).not.toHaveBeenCalled();
  });

  it("refuses a new file name already in the folder, before an empty editor could replace it, and says so aloud", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findAllByTestId("file-entry");
    fireEvent.click(screen.getByTestId("new-file"));
    const input = await screen.findByTestId("new-file-name");
    // The live region is there before any words, so they are announced.
    const hint = screen.getByTestId("new-file-hint");
    expect(hint).toHaveAttribute("aria-live", "polite");
    expect(hint.textContent).toBe("");
    expect(input).toHaveAttribute("aria-describedby", hint.id);
    fireEvent.change(input, { target: { value: "notes.txt" } });
    expect(screen.getByTestId("new-file-hint")).toHaveTextContent(
      "A file named notes.txt already exists here. Open it from the list to edit it, or choose another name.",
    );
    expect(screen.getByTestId("new-file-create")).toBeDisabled();
    fireEvent.submit(input.closest("form"));
    expect(screen.queryByTestId("file-viewer")).toBeNull();
    fireEvent.change(input, { target: { value: "fresh.txt" } });
    expect(screen.getByTestId("new-file-hint").textContent).toBe("");
    expect(screen.getByTestId("new-file-create")).not.toBeDisabled();
  });

  // A path such as src/notes.txt creates the file in a folder below this one,
  // as production does. That folder's list is not on screen, so Create reads
  // it first: a file of that name there is never replaced by an empty one.
  describe("New file with a path", () => {
    const SRC = [{ name: "main.py", size: 12, mode: "-rw-r--r--" }];
    function byFolder(folders) {
      return vi.fn(async (_id, path = "") => {
        const answer = folders[path];
        if (answer instanceof Error) throw answer;
        if (!answer) {
          throw new OmegaError({
            state: "not-found",
            status: 404,
            code: "not_found",
          });
        }
        return { path, entries: answer };
      });
    }
    async function typePath(api, value) {
      renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
      await screen.findAllByTestId("file-entry");
      fireEvent.click(screen.getByTestId("new-file"));
      const input = await screen.findByTestId("new-file-name");
      fireEvent.change(input, { target: { value } });
      return input;
    }
    // The hint is always on the page, so wait for its words, not for it.
    async function hintSays(words) {
      await waitFor(() =>
        expect(screen.getByTestId("new-file-hint")).toHaveTextContent(words),
      );
    }

    it("creates the file in a folder below this one once that folder's list shows no such file", async () => {
      const api = fakeApi({ listFiles: byFolder({ "": ENTRIES, src: SRC }) });
      const input = await typePath(api, "src/notes.txt");
      expect(screen.getByTestId("new-file-hint").textContent).toBe("");
      expect(screen.getByTestId("new-file-create")).not.toBeDisabled();
      fireEvent.submit(input.closest("form"));
      const viewer = await screen.findByTestId("file-viewer");
      expect(viewer).toHaveTextContent("src/notes.txt");
      expect(api.listFiles).toHaveBeenCalledWith(ID, "src");
    });

    it("refuses a path whose file already exists in its folder, from that folder's own list", async () => {
      const api = fakeApi({
        listFiles: byFolder({
          "": ENTRIES,
          src: [...SRC, { name: "notes.txt", size: 6, mode: "-rw-r--r--" }],
        }),
      });
      const input = await typePath(api, "src/notes.txt");
      fireEvent.submit(input.closest("form"));
      await hintSays(
        "A file named notes.txt already exists in src. Open it from that folder to edit it, or choose another name.",
      );
      expect(screen.queryByTestId("file-viewer")).toBeNull();
    });

    it("refuses a path that names a folder already there", async () => {
      const api = fakeApi({
        listFiles: byFolder({
          "": ENTRIES,
          src: [{ name: "lib", size: 64, mode: "drwxr-xr-x", directory: true }],
        }),
      });
      const input = await typePath(api, "src/lib");
      fireEvent.submit(input.closest("form"));
      await hintSays(
        "A folder named lib already exists in src. Choose another name.",
      );
      expect(screen.queryByTestId("file-viewer")).toBeNull();
    });

    it("goes ahead when the folder isn't there yet, as before", async () => {
      const api = fakeApi({ listFiles: byFolder({ "": ENTRIES }) });
      const input = await typePath(api, "docs/notes.txt");
      fireEvent.submit(input.closest("form"));
      const viewer = await screen.findByTestId("file-viewer");
      expect(viewer).toHaveTextContent("docs/notes.txt");
    });

    it("creates nothing when that folder's list can't be read, says so, and lets Create try again", async () => {
      const api = fakeApi({
        listFiles: byFolder({
          "": ENTRIES,
          src: new OmegaError({ state: "unavailable", status: 503 }),
        }),
      });
      const input = await typePath(api, "src/notes.txt");
      fireEvent.submit(input.closest("form"));
      await hintSays(
        "The list of src couldn't be read, so it can't be checked whether notes.txt is already there. Nothing was created. Try again.",
      );
      expect(screen.queryByTestId("file-viewer")).toBeNull();
      expect(screen.getByTestId("new-file-create")).not.toBeDisabled();
      api.listFiles.mockImplementation(byFolder({ "": ENTRIES, src: SRC }));
      fireEvent.submit(input.closest("form"));
      const viewer = await screen.findByTestId("file-viewer");
      expect(viewer).toHaveTextContent("src/notes.txt");
    });

    it("reads a run of slashes as one, as production does", async () => {
      const api = fakeApi({ listFiles: byFolder({ "": ENTRIES, src: SRC }) });
      const input = await typePath(api, "src//notes.txt");
      expect(screen.getByTestId("new-file-hint").textContent).toBe("");
      fireEvent.submit(input.closest("form"));
      const viewer = await screen.findByTestId("file-viewer");
      expect(viewer).toHaveTextContent("src/notes.txt");
      expect(api.listFiles).toHaveBeenCalledWith(ID, "src");
    });

    it("says why, with no Try again, when reading that folder's list again won't help", async () => {
      const api = fakeApi({
        listFiles: byFolder({
          "": ENTRIES,
          src: new OmegaError({
            state: "refused",
            status: 403,
            message: "Your access in this workspace doesn't include this.",
          }),
        }),
      });
      const input = await typePath(api, "src/notes.txt");
      fireEvent.submit(input.closest("form"));
      await hintSays(
        "The list of src can't be read, so nothing was created. Your access in this workspace doesn't include this.",
      );
      expect(screen.getByTestId("new-file-hint")).not.toHaveTextContent(
        "Try again",
      );
      expect(screen.queryByTestId("file-viewer")).toBeNull();
    });

    it("checks a refused path again once its name is edited, so a file deleted since can be created", async () => {
      const api = fakeApi({
        listFiles: byFolder({
          "": ENTRIES,
          src: [...SRC, { name: "notes.txt", size: 6, mode: "-rw-r--r--" }],
        }),
      });
      const input = await typePath(api, "src/notes.txt");
      fireEvent.submit(input.closest("form"));
      await hintSays("A file named notes.txt already exists in src.");
      expect(screen.getByTestId("new-file-create")).toBeDisabled();
      // notes.txt is deleted elsewhere; editing the name asks again on Create.
      api.listFiles.mockImplementation(byFolder({ "": ENTRIES, src: SRC }));
      fireEvent.change(input, { target: { value: "src/notes.tx" } });
      fireEvent.change(input, { target: { value: "src/notes.txt" } });
      expect(screen.getByTestId("new-file-create")).not.toBeDisabled();
      fireEvent.submit(input.closest("form"));
      const viewer = await screen.findByTestId("file-viewer");
      expect(viewer).toHaveTextContent("src/notes.txt");
    });

    it.each([
      ["/notes.txt", "Start the path in this folder, without a leading /."],
      ["src/", "Each part of the path needs a name."],
      ["src/../notes.txt", "A path cannot use . or .. as a part."],
      ["src\t/notes.txt", "A name cannot contain control characters."],
    ])("refuses the path %s and says why", async (value, why) => {
      const api = fakeApi({ listFiles: byFolder({ "": ENTRIES, src: SRC }) });
      const input = await typePath(api, value);
      expect(screen.getByTestId("new-file-hint")).toHaveTextContent(why);
      expect(screen.getByTestId("new-file-create")).toBeDisabled();
      fireEvent.submit(input.closest("form"));
      expect(screen.queryByTestId("file-viewer")).toBeNull();
      expect(api.listFiles).not.toHaveBeenCalledWith(ID, "src");
    });
  });

  it("creates no new file until the folder's list has loaded", () => {
    const api = fakeApi({ listFiles: vi.fn(() => new Promise(() => {})) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    fireEvent.click(screen.getByTestId("new-file"));
    fireEvent.change(screen.getByTestId("new-file-name"), {
      target: { value: "notes.txt" },
    });
    expect(screen.getByTestId("new-file-hint")).toHaveTextContent(
      "This folder's list hasn't loaded yet, so the name can't be checked.",
    );
    expect(screen.getByTestId("new-file-create")).toBeDisabled();
    // Waiting is the remedy here, so nothing offers to read the list again.
    expect(screen.queryByTestId("new-file-retry")).toBeNull();
  });

  it("says a folder whose list couldn't be read can't check a new file's name, and reads it again on Try again", async () => {
    const api = fakeApi({
      listFiles: vi
        .fn()
        .mockRejectedValueOnce(
          new OmegaError({
            state: "error",
            status: 500,
            code: "internal",
            message: "server words",
          }),
        )
        .mockResolvedValue({ path: "", entries: ENTRIES }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findByTestId("sandbox-error-state");
    fireEvent.click(screen.getByTestId("new-file"));
    const input = screen.getByTestId("new-file-name");
    fireEvent.change(input, { target: { value: "notes.txt" } });
    expect(screen.getByTestId("new-file-hint")).toHaveTextContent(
      "This folder's list couldn't be read, so the name can't be checked.",
    );
    expect(screen.getByTestId("new-file-create")).toBeDisabled();
    // Pressed from the keyboard: the button goes away, focus goes to the name.
    const retry = screen.getByTestId("new-file-retry");
    retry.focus();
    expect(retry).toHaveFocus();
    fireEvent.click(retry);
    expect(input).toHaveFocus();
    await waitFor(() => expect(api.listFiles).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByTestId("new-file-hint")).toHaveTextContent(
        "A file named notes.txt already exists here. Open it from the list to edit it, or choose another name.",
      ),
    );
    expect(screen.queryByTestId("new-file-retry")).toBeNull();
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "fresh.txt" } });
    expect(screen.getByTestId("new-file-create")).not.toBeDisabled();
  });

  it("offers New file Try again, as the list's error panel does, when the sign-in couldn't be confirmed", async () => {
    const api = fakeApi({
      listFiles: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "unauthenticated",
          title: "Omega couldn't confirm your sign-in",
          message:
            "Future AGI couldn't confirm your sign-in with Omega. Try again in a moment. If it keeps happening, contact Future AGI support.",
        });
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    const panel = await screen.findByTestId("sandbox-error-state");
    expect(within(panel).queryByRole("button", { name: "Reload" })).toBeNull();
    expect(
      within(panel).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("new-file"));
    fireEvent.change(screen.getByTestId("new-file-name"), {
      target: { value: "notes.txt" },
    });
    expect(screen.getByTestId("new-file-hint").textContent).toBe(
      "This folder's list couldn't be read, so the name can't be checked.",
    );
    expect(screen.getByTestId("new-file-retry")).toBeInTheDocument();
  });

  it("points New file to Reload, as the list's error panel does, when only a reload can cure the read", async () => {
    const api = fakeApi({
      listFiles: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "role_out_of_date",
          title: "Your role just changed",
          message:
            "Your role in this workspace changed a moment ago. Reload the page to continue with your current role.",
        });
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    const panel = await screen.findByTestId("sandbox-error-state");
    expect(
      within(panel).getByRole("button", { name: "Reload" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("new-file"));
    fireEvent.change(screen.getByTestId("new-file-name"), {
      target: { value: "notes.txt" },
    });
    expect(screen.getByTestId("new-file-hint").textContent).toBe(
      "This folder's list couldn't be read, so the name can't be checked. Reload the page and try again.",
    );
    // Reading the list again gets the same refusal, so nothing offers it.
    expect(screen.queryByTestId("new-file-retry")).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByTestId("new-file-create")).toBeDisabled();
  });

  it("offers New file no retry for a folder whose read won't change by asking again", async () => {
    const api = fakeApi({
      listFiles: vi.fn(async () => {
        throw new OmegaError({
          state: "not-found",
          status: 404,
          code: "not_found",
          message: "server words",
        });
      }),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    await screen.findByTestId("sandbox-error-state");
    fireEvent.click(screen.getByTestId("new-file"));
    fireEvent.change(screen.getByTestId("new-file-name"), {
      target: { value: "notes.txt" },
    });
    expect(screen.getByTestId("new-file-hint").textContent).toBe(
      "This folder's list couldn't be read, so the name can't be checked.",
    );
    expect(screen.queryByTestId("new-file-retry")).toBeNull();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
    expect(screen.getByTestId("new-file-create")).toBeDisabled();
  });

  // The sign-in copy says "Try again in a moment"; the file panels showed it
  // with no action at all.
  describe("when the sign-in couldn't be confirmed for one file", () => {
    function signInRefusal() {
      return new OmegaError({
        state: "refused",
        status: 403,
        code: "unauthenticated",
        title: "Omega couldn't confirm your sign-in",
        message:
          "Future AGI couldn't confirm your sign-in with Omega. Try again in a moment. If it keeps happening, contact Future AGI support.",
      });
    }

    it("offers Try again in the file viewer, which reads the file again", async () => {
      const readFile = vi.fn(async () => {
        throw signInRefusal();
      });
      const api = fakeApi({ listFiles: listing(ENTRIES), readFile });
      renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
      fireEvent.click((await screen.findAllByTestId("file-entry"))[1]);
      const viewer = await screen.findByTestId("file-viewer");
      const panel = await within(viewer).findByTestId("sandbox-error-state");
      expect(panel).toHaveTextContent("Omega couldn't confirm your sign-in");
      expect(
        within(panel).queryByRole("button", { name: "Reload" }),
      ).toBeNull();
      const again = within(panel).getByRole("button", { name: "Try again" });
      const reads = readFile.mock.calls.length;
      fireEvent.click(again);
      await waitFor(() =>
        expect(readFile.mock.calls.length).toBeGreaterThan(reads),
      );
    });

    it("offers Try again on an image preview, which fetches the image again", async () => {
      const downloadFile = vi.fn(async () => {
        throw signInRefusal();
      });
      const api = fakeApi({ listFiles: listing(ENTRIES), downloadFile });
      renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
      fireEvent.click((await screen.findAllByTestId("file-entry"))[0]);
      const viewer = await screen.findByTestId("file-viewer");
      const panel = await within(viewer).findByTestId("sandbox-error-state");
      const again = within(panel).getByRole("button", { name: "Try again" });
      const fetches = downloadFile.mock.calls.length;
      fireEvent.click(again);
      await waitFor(() =>
        expect(downloadFile.mock.calls.length).toBeGreaterThan(fetches),
      );
    });
  });

  it("sorts the list by name or size from its headings, folders first", async () => {
    const api = fakeApi({ listFiles: listing(ENTRIES) });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    const order = () =>
      screen.getAllByTestId("file-entry").map((entry) => entry.dataset.name);
    await screen.findAllByTestId("file-entry");
    // The sandbox's own order until a heading is chosen.
    expect(order()).toEqual(["chart.png", "notes.txt", "src"]);
    fireEvent.click(screen.getByTestId("files-sort-name"));
    expect(order()).toEqual(["src", "chart.png", "notes.txt"]);
    fireEvent.click(screen.getByTestId("files-sort-name"));
    expect(order()).toEqual(["src", "notes.txt", "chart.png"]);
    expect(screen.getByTestId("files-sort-name")).toHaveTextContent(
      "Name, sorted descending",
    );
    fireEvent.click(screen.getByTestId("files-sort-size"));
    expect(order()).toEqual(["src", "chart.png", "notes.txt"]);
    fireEvent.click(screen.getByTestId("files-sort-size"));
    expect(order()).toEqual(["src", "notes.txt", "chart.png"]);
    expect(screen.getByTestId("files-columns")).toHaveTextContent("Size");
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
