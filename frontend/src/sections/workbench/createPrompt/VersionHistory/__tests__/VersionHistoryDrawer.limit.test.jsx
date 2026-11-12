import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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
  default: ({ version, checkboxMessage }) => (
    <div data-testid={`card-${version.template_version}`}>
      {checkboxMessage}
    </div>
  ),
}));

const workbench = {
  versionHistoryOpen: true,
  setVersionHistoryOpen: vi.fn(),
  selectedVersions: [{ version: "V1" }],
  applyCompare: vi.fn(),
};

// FA-PROMPTS-04: the compare limit message is plain and keeps its number.
describe("VersionHistoryDrawer compare limit copy", () => {
  it("explains the limit of 3 versions in plain words", async () => {
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
    const card = await screen.findByTestId("card-V2");
    expect(card).toHaveTextContent(
      "You can compare up to 3 versions. Deselect one to select this version.",
    );
    expect(card.textContent).not.toMatch(/upto/);
  });
});
