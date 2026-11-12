import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import {
  AA_TEXT,
  PROMPT_THEMES,
  contrastOnPaper,
  isZero,
  rowOf,
} from "src/sections/workbench/__tests__/promptsContrast";
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
  promptName: "Support reply",
  promptGeneratingStatus: [false],
  getStreamingIds: () => [],
};

function renderHeader(theme = PROMPT_THEMES[0][1]) {
  render(
    <ThemeProvider theme={theme}>
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
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

// At 390 px the action group (Commit, More, Variables, Run
// Prompt, Run on a dataset) could neither shrink nor wrap, so it ran past the
// right edge. jsdom has no layout: these pin the phone-width (base) styles;
// the desktop override sits in a media query jsdom does not apply.
describe("Prompt editor header at phone width", () => {
  it("lets the action group wrap and shrink so Commit and Run Prompt stay on screen", () => {
    renderHeader();
    const commit = screen.getByRole("button", { name: "Commit" });
    const run = screen.getByRole("button", { name: "Run Prompt" });
    const actions = rowOf(commit, run);
    const style = getComputedStyle(actions);
    expect(style.flexWrap).toBe("wrap");
    expect(isZero(style.minWidth)).toBe(true);
    expect(style.flexShrink).not.toBe("0");
    expect(style.maxWidth).toBe("100%");
    expect(style.marginLeft).toBe("auto");
  });

  it("lets the name, version and chips wrap instead of squeezing the name to nothing", () => {
    renderHeader();
    const name = screen.getByRole("heading", { level: 1 });
    const draft = screen.getByText("Draft");
    const titleRow = rowOf(name, draft);
    expect(getComputedStyle(titleRow).flexWrap).toBe("wrap");
    expect(isZero(getComputedStyle(titleRow).minWidth)).toBe(true);
  });
});

// Enabled controls and metadata used text.disabled (about
// 2.6:1), so they looked disabled and failed text contrast.
describe.each(PROMPT_THEMES)(
  "Readable header text (%s theme)",
  (_mode, theme) => {
    it("paints Add new prompt in a readable colour", () => {
      renderHeader(theme);
      const add = screen.getByRole("button", { name: /add new prompt/i });
      expect(add).toBeEnabled();
      expect(contrastOnPaper(add, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it("shows when the prompt was last saved in a readable colour", () => {
      renderHeader(theme);
      const saved = screen.getByText(/^Last saved /);
      expect(contrastOnPaper(saved, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });
  },
);
