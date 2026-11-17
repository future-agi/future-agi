import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { rowOf } from "src/sections/workbench/__tests__/promptsContrast";
import { PromptWorkbenchContext } from "../../WorkbenchContext";
import VersionHistoryDrawer from "../VersionHistoryDrawer";

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
          results: ["V1", "V2"].map((v) => ({
            id: `id-${v}`,
            template_version: v,
            is_draft: false,
          })),
        },
      }),
    ),
  },
  endpoints: {
    develop: { runPrompt: { getPromptVersions: () => "/prompt-history/" } },
  },
}));
vi.mock("../VersionCard", () => ({
  default: ({ version }) => (
    <div data-testid={`card-${version.template_version}`} />
  ),
}));

const workbench = {
  versionHistoryOpen: true,
  setVersionHistoryOpen: vi.fn(),
  selectedVersions: [{ version: "V1" }],
  applyCompare: vi.fn(),
};

async function renderDrawer() {
  render(
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
  await screen.findByTestId("card-V2");
}

// The History drawer was a fixed 960 px, so below 960 px its
// left side (title, tabs, version names, Select to compare) sat off screen.
describe("History drawer on narrow screens", () => {
  it("keeps its 960 px width but never grows past the screen", async () => {
    await renderDrawer();
    const paper = document.querySelector(".MuiDrawer-paper");
    expect(getComputedStyle(paper).width).toBe("960px");
    expect(getComputedStyle(paper).maxWidth).toBe("100vw");
  });

  it("wraps Select to compare under the tabs instead of past the edge", async () => {
    await renderDrawer();
    const tab = screen.getByRole("tab", { name: "History" });
    const compare = screen.getByRole("button", { name: "Select to compare" });
    expect(getComputedStyle(rowOf(tab, compare)).flexWrap).toBe("wrap");
  });

  it("names its close button", async () => {
    await renderDrawer();
    expect(
      screen.getByRole("button", { name: "Close history" }),
    ).toBeInTheDocument();
  });
});
