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

function renderHeader(theme) {
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

describe("Prompt editor Last saved line", () => {
  it.each(["light", "dark"])("reads at 4.5:1 or better in %s", (mode) => {
    const theme = appTheme(mode);
    // Metadata such as "Last saved" is drawn in text.tertiary.
    const { tertiary } = theme.palette.text;
    expect(
      getContrastRatio(tertiary, theme.palette.background.paper),
    ).toBeGreaterThanOrEqual(4.5);

    renderHeader(theme);

    // text.disabled reads at about 2.6:1 on paper; metadata uses text.tertiary.
    const line = screen.getByText(/^Last saved /);
    expect(getComputedStyle(line).color).toBe(rgb(tertiary));
  });
});
