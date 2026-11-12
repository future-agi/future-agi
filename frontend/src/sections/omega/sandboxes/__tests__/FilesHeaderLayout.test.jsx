import React from "react";
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FilesTab from "../detail/FilesTab";
import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

// Screen pass (owner: "items are getting cut"): in the Files list's narrow
// column the toolbar sat beside a long folder path, which then wrapped one
// segment per line, a long folder name centred inside its own button.
describe("Files list header", () => {
  async function renderFiles() {
    const api = fakeApi({
      listFiles: vi.fn(async () => ({
        path: "",
        entries: [{ name: "notes.txt", size: 6, mode: "-rw-r--r--" }],
      })),
    });
    renderSandboxes(<FilesTab sandbox={sandboxView()} />, { api });
    return screen.findByTestId("files-list");
  }

  it("lets the toolbar move under a long folder path instead of squeezing it", async () => {
    const header = (await renderFiles()).firstElementChild;
    expect(header).toHaveStyle({ flexWrap: "wrap" });
    expect(screen.getByTestId("files-breadcrumbs")).toHaveStyle({
      flexGrow: "1",
      minWidth: "0",
    });
  });

  it("keeps the toolbar on the right when it moves under the folder path", async () => {
    await renderFiles();
    expect(screen.getByTestId("file-upload").parentElement).toHaveStyle({
      marginLeft: "auto",
    });
  });

  it("wraps a long folder name left-aligned, as text, not centred in its button", async () => {
    await renderFiles();
    expect(screen.getByRole("button", { name: "/workspace" })).toHaveStyle({
      textAlign: "left",
      overflowWrap: "anywhere",
    });
  });
});
