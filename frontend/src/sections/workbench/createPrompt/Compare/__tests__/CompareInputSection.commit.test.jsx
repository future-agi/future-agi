import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
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

const workbenchWith = (version) => ({
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
});

function renderColumn(version, setSaveCommitOpen = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PromptWorkbenchContext.Provider value={workbenchWith(version)}>
        <MemoryRouter initialEntries={["/dashboard/workbench/create/p1"]}>
          <Routes>
            <Route
              path="/dashboard/workbench/create/:id"
              element={
                <CompareInputSection
                  index={1}
                  promptVersion={version}
                  setSaveCommitOpen={setSaveCommitOpen}
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
  return setSaveCommitOpen;
}

// FA-PROMPTS-06: a column's commit icon never looks enabled while it does
// nothing; once the column has run it opens the commit dialog for it.
describe("CompareInputSection column commit", () => {
  it("is disabled for a draft column that has not run, and says what to do", () => {
    const setSaveCommitOpen = renderColumn({
      version: "V3",
      isDraft: true,
      id: "b",
    });
    const commit = screen.getByRole("button", { name: /commit/i });
    expect(commit).toBeDisabled();
    expect(
      screen.getByLabelText("Run this version first, then commit it here."),
    ).toBeInTheDocument();
    fireEvent.click(commit);
    expect(setSaveCommitOpen).not.toHaveBeenCalled();
  });

  it("is enabled once the column has run and opens commit for that version", () => {
    const setSaveCommitOpen = renderColumn({
      version: "V3",
      isDraft: false,
      isDefault: false,
      id: "b",
    });
    const commit = screen.getByRole("button", { name: /commit/i });
    expect(commit).toBeEnabled();
    fireEvent.click(commit);
    expect(setSaveCommitOpen).toHaveBeenCalledWith(
      expect.objectContaining({ version: "V3", isDraft: false }),
    );
  });
});
