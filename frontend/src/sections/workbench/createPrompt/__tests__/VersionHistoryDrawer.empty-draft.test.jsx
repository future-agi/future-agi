import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../WorkbenchContext";
import VersionHistoryDrawer from "../VersionHistory/VersionHistoryDrawer";

// cu-fa-pr-01: History's Compare compares the versions picked there. The open
// version joins as the first pane only when it holds content; an open empty
// draft (one left behind by an earlier edit) is neither ticked nor locked,
// so it never joins on its own.

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() =>
      Promise.resolve({
        data: {
          next: null,
          current_page: 1,
          results: [
            { id: "id-v3", template_version: "v3", is_draft: false },
            { id: "id-v2", template_version: "v2", is_draft: true },
            { id: "id-v1", template_version: "v1", is_draft: false },
          ],
        },
      }),
    ),
  },
  endpoints: {
    develop: { runPrompt: { getPromptVersions: () => "/prompt-history/" } },
  },
}));
vi.mock("../VersionHistory/VersionCard", () => ({
  default: ({ version, showCheckbox, checked, disableCheckbox, setChecked }) =>
    showCheckbox ? (
      <input
        type="checkbox"
        aria-label={`pick ${version.template_version}`}
        checked={Boolean(checked)}
        disabled={Boolean(disableCheckbox)}
        onChange={setChecked}
      />
    ) : (
      <div>{version.template_version}</div>
    ),
}));

function renderDrawer(workbench) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PromptWorkbenchContext.Provider value={workbench}>
        <MemoryRouter initialEntries={["/dashboard/workbench/create/p1"]}>
          <Routes>
            <Route
              path="/dashboard/workbench/create/:id"
              element={<VersionHistoryDrawer />}
            />
          </Routes>
        </MemoryRouter>
      </PromptWorkbenchContext.Provider>
    </QueryClientProvider>,
  );
}

const workbenchWith = (open, isEmptyPrompt) => ({
  versionHistoryOpen: true,
  setVersionHistoryOpen: vi.fn(),
  selectedVersions: [open],
  isEmptyPrompt,
  applyCompare: vi.fn(),
});

describe("VersionHistoryDrawer Compare with an open empty draft", () => {
  it("does not tick the empty draft and compares only the two picked versions", async () => {
    const workbench = workbenchWith({ version: "v2", isDraft: true }, [0]);
    renderDrawer(workbench);
    await screen.findByText("v3");
    fireEvent.click(screen.getByRole("button", { name: "Select to compare" }));

    const draft = screen.getByRole("checkbox", { name: "pick v2" });
    expect(draft).not.toBeChecked();
    expect(draft).toBeEnabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "pick v3" }));
    // One picked version is nothing to compare with.
    expect(
      screen.queryByRole("button", { name: "Compare" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "pick v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    expect(workbench.applyCompare).toHaveBeenCalledTimes(1);
    expect(
      workbench.applyCompare.mock.calls[0][0].map((v) => v.template_version),
    ).toEqual(["v3", "v1"]);
  });

  it("still ticks and locks an open version that holds content", async () => {
    const workbench = workbenchWith({ version: "v3", isDraft: false }, []);
    renderDrawer(workbench);
    await screen.findByText("v3");
    fireEvent.click(screen.getByRole("button", { name: "Select to compare" }));

    const open = screen.getByRole("checkbox", { name: "pick v3" });
    expect(open).toBeChecked();
    expect(open).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "pick v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    expect(
      workbench.applyCompare.mock.calls[0][0].map((v) => v.template_version),
    ).toEqual(["v1"]);
  });
});
