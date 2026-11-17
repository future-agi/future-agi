import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import {
  ThemeProvider,
  createTheme,
  getContrastRatio,
} from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
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

const version = {
  version: "V2",
  isDraft: false,
  id: "b",
  lastSaved: "2026-11-11T01:52:52.254296Z",
};

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

// The app's theme, built as theme/index.jsx builds it.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

const rgb = (hex) =>
  `rgb(${hex
    .slice(1)
    .match(/../g)
    .map((v) => parseInt(v, 16))
    .join(", ")})`;

describe("Compare column Last saved line", () => {
  it.each(["light", "dark"])("reads at 4.5:1 or better in %s", (mode) => {
    const theme = appTheme(mode);
    // Metadata such as "Last saved" is drawn in text.tertiary.
    const { tertiary } = theme.palette.text;
    expect(
      getContrastRatio(tertiary, theme.palette.background.paper),
    ).toBeGreaterThanOrEqual(4.5);

    render(
      <ThemeProvider theme={theme}>
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
        </QueryClientProvider>
      </ThemeProvider>,
    );

    // text.disabled reads at about 2.6:1 on paper; metadata uses text.tertiary.
    const line = screen.getByText(/^Last saved /);
    expect(getComputedStyle(line).color).toBe(rgb(tertiary));
  });
});
