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
    versions: [{ id: "v1", template_version: "V1", is_draft: true }],
    refetch: vi.fn(),
  }),
}));

const workbench = {
  currentTab: "Playground",
  setCurrentTab: vi.fn(),
  modelConfig: [{ model: "gpt-4o" }],
  prompts: [
    { prompts: [{ role: "user", content: [{ type: "text", text: "Hi" }] }] },
  ],
  results: [{ output: [] }],
  selectedVersions: [
    {
      version: "V1",
      isDraft: true,
      id: "a",
      lastSaved: "2026-11-11T01:52:52.254296Z",
    },
  ],
  isEmptyPrompt: [],
  variableData: {},
  promptName: "cu screenpass mwbgrmtb",
  promptGeneratingStatus: [false],
  getStreamingIds: () => [],
};

function renderHeader() {
  render(
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

// SPFIX-r1 PROMPTS 2: at 1024 px the header row ran 840 px in 824, the
// prompt name shrank to nothing and Commit covered the V1 "Draft" chip.
describe("Prompt editor header at narrow widths", () => {
  it("wraps the actions onto their own right-aligned row instead of covering the name and chips", () => {
    renderHeader();
    const name = screen.getByRole("heading", { level: 1 });
    expect(name).toHaveTextContent("cu screenpass mwbgrmtb");
    expect(getComputedStyle(name).textOverflow).toBe("ellipsis");
    expect(screen.getByText("Draft")).toBeInTheDocument();

    const commit = screen.getByRole("button", { name: "Commit" });
    let row = name.parentElement;
    while (row && !row.contains(commit)) row = row.parentElement;
    expect(getComputedStyle(row).flexWrap).toBe("wrap");

    const actions = [...row.children].find((el) => el.contains(commit));
    expect(getComputedStyle(actions).marginLeft).toBe("auto");
  });

  it("keeps the line under the header inside the page", () => {
    renderHeader();
    const name = screen.getByRole("heading", { level: 1 });
    const commit = screen.getByRole("button", { name: "Commit" });
    let row = name.parentElement;
    while (row && !row.contains(commit)) row = row.parentElement;

    const line = row.nextElementSibling;
    expect(line.tagName).toBe("HR");
    expect(getComputedStyle(line).marginRight).not.toBe("-16px");
    expect(getComputedStyle(line).marginLeft).not.toBe("-16px");
  });
});
