import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../../WorkbenchContext";
import PromptActions from "../PromptActions";

const authState = { user: null, role: "Owner" };

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: [{ id: "v1", template_version: "v1", is_draft: false }],
    refetch: vi.fn(),
  }),
}));

const workbench = {
  currentTab: "Playground",
  setCurrentTab: vi.fn(),
  modelConfig: [],
  prompts: [],
  results: [],
  selectedVersions: [],
  isEmptyPrompt: [],
  variableData: {},
  promptName: "Prompt",
  getStreamingIds: () => [],
};

function renderActions() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PromptWorkbenchContext.Provider value={workbench}>
        <MemoryRouter initialEntries={["/dashboard/workbench/create/prompt-1"]}>
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
}

describe("PromptActions Simulation tab", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
  });

  it("is hidden for an org restricted to Environments", () => {
    authState.user.restrict_simulate_to_environments = true;
    renderActions();
    expect(screen.getByRole("tab", { name: /Playground/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Simulation/ })).toBeNull();
  });

  it.each([false, undefined])("is shown when the flag is %s", (flag) => {
    authState.user.restrict_simulate_to_environments = flag;
    renderActions();
    expect(screen.getByRole("tab", { name: /Simulation/ })).toBeInTheDocument();
  });
});
