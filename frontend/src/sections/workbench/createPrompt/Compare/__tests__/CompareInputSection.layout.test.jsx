import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PromptWorkbenchContext } from "../../WorkbenchContext";
import CompareInputSection from "../CompareInputSection";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));
vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({ versions: [] }),
}));
vi.mock("../../Playground/PromptSection", () => ({
  default: () => <div data-testid="prompt-section" />,
}));

const version = { version: "V2", isDraft: false, id: "b" };

const workbench = {
  prompts: [
    { prompts: [{ role: "user", content: [{ type: "text", text: "Hi" }] }] },
    { prompts: [{ role: "user", content: [{ type: "text", text: "Hi" }] }] },
  ],
  placeholders: [[], []],
  modelConfig: [{ model: "gpt-4o" }, { model: "gpt-4o" }],
  selectedVersions: [{ version: "V1", id: "a" }, version],
  saveAndRun: vi.fn(),
  removeFromCompare: vi.fn(),
  setOpenSelectModel: vi.fn(),
  isEmptyPrompt: [],
  variableData: {},
  setVariableDrawerOpen: vi.fn(),
  promptGeneratingStatus: [false, false],
  setLoadingStatusByIndex: vi.fn(),
  isAddingDraft: false,
  getStreamingIds: () => [],
  setStreamingIdForVersion: vi.fn(),
  pushStoppedIds: vi.fn(),
  closeSocketByIndex: vi.fn(),
  templateFormat: "mustache",
  setPromptsByIndex: vi.fn(),
  setPlaceholdersByIndex: vi.fn(),
  setModelConfigByIndex: vi.fn(),
};

// SPFIX-r1 PROMPTS 3: at 1280 px the Output panel cut each column's User
// message box in half, and the column could not scroll to it.
describe("Compare column inputs", () => {
  it("scroll inside the column so the message boxes are never cut off", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PromptWorkbenchContext.Provider value={workbench}>
          <MemoryRouter initialEntries={["/dashboard/workbench/create/p1"]}>
            <Routes>
              <Route
                path="/dashboard/workbench/create/:id"
                element={
                  <CompareInputSection
                    index={1}
                    promptVersion={version}
                    setSaveCommitOpen={vi.fn()}
                    isSync={false}
                    setIsSync={vi.fn()}
                    syncSystemPrompt={vi.fn()}
                  />
                }
              />
            </Routes>
          </MemoryRouter>
        </PromptWorkbenchContext.Provider>
      </QueryClientProvider>,
    );
    const inputs = screen.getByTestId("prompt-section").parentElement;
    expect(getComputedStyle(inputs).overflowY).toBe("auto");
    expect(getComputedStyle(inputs).minHeight).toMatch(/^0(px)?$/);
  });
});
