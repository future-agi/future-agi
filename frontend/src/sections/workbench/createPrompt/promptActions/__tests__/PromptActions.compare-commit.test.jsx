import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../../WorkbenchContext";
import PromptActions from "../PromptActions";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));
vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: [
      { id: "v1", template_version: "V1", is_draft: false },
      { id: "v3", template_version: "V3", is_draft: true },
    ],
    refetch: vi.fn(),
  }),
}));

const workbench = {
  currentTab: "Playground",
  setCurrentTab: vi.fn(),
  modelConfig: [{ model: "gpt-4o" }, { model: "gpt-4o" }],
  prompts: [
    { prompts: [{ role: "user", content: [{ type: "text", text: "Hi" }] }] },
    { prompts: [{ role: "user", content: [{ type: "text", text: "Hi" }] }] },
  ],
  results: [{ output: [] }, { output: [] }],
  selectedVersions: [
    { version: "V1", isDraft: false, id: "a" },
    { version: "V3", isDraft: true, id: "b" },
  ],
  isEmptyPrompt: [],
  variableData: {},
  promptName: "Prompt",
  promptGeneratingStatus: [false, false],
  getStreamingIds: () => [],
};

// FA-PROMPTS-06: with several columns open the top Commit stays disabled,
// and its hint points at the working path (the column's commit icon)
// instead of telling the user to close the other columns.
describe("PromptActions Commit with several compare columns", () => {
  it("points at the column's commit icon", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PromptWorkbenchContext.Provider value={workbench}>
          <MemoryRouter
            initialEntries={["/dashboard/workbench/create/prompt-1"]}
          >
            <Routes>
              <Route
                path="/dashboard/workbench/create/:id"
                element={<PromptActions />}
              />
            </Routes>
          </MemoryRouter>
        </PromptWorkbenchContext.Provider>
      </QueryClientProvider>,
    );
    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();
    const hint = screen.getByLabelText(/commit icon on its column/);
    expect(hint).toBeInTheDocument();
    expect(hint.getAttribute("aria-label")).not.toMatch(/remove the others/);
  });
});
