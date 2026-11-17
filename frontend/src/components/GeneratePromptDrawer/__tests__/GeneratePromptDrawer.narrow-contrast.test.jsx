import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AA_TEXT,
  PROMPT_THEMES,
  contrastOnPaper,
} from "src/sections/workbench/__tests__/promptsContrast";
import GeneratePromptDrawer from "../GeneratePromptDrawer";

vi.mock("src/sections/workbench/createPrompt/hooks/usePromptStreamUrl", () => ({
  usePromptStreamUrl: () => vi.fn(),
}));
vi.mock("src/sections/workbench/createPrompt/common", () => ({
  handleAuthFailClose: vi.fn(),
  handleWsErrorFrame: vi.fn(),
  runPromptOverSocket: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

function renderDrawer(theme = PROMPT_THEMES[0][1]) {
  render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={new QueryClient()}>
        <GeneratePromptDrawer
          open
          onClose={vi.fn()}
          promptFor={0}
          onApplyPrompt={vi.fn()}
        />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

// The Generate drawer was a fixed 640 px, so on a phone its
// title and the left of the form sat off screen.
describe("Generate a prompt drawer on narrow screens", () => {
  it("keeps its 640 px width but never grows past the screen", () => {
    renderDrawer();
    expect(screen.getByText("Generate a prompt")).toBeInTheDocument();
    const paper = document.querySelector(".MuiDrawer-paper");
    expect(getComputedStyle(paper).width).toBe("640px");
    expect(getComputedStyle(paper).maxWidth).toBe("100vw");
  });

  it("names its close button", () => {
    renderDrawer();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });
});

// Cancel and the example-task icons used text.disabled, so
// they looked disabled and failed contrast.
describe.each(PROMPT_THEMES)(
  "Readable Generate drawer controls (%s theme)",
  (_mode, theme) => {
    it("paints Cancel in a readable colour", () => {
      renderDrawer(theme);
      const cancel = screen.getByRole("button", { name: "Cancel" });
      expect(cancel).toBeEnabled();
      const label = screen.getByText("Cancel");
      expect(contrastOnPaper(label, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it("paints the example-task icons in a readable colour", () => {
      renderDrawer(theme);
      const example = screen.getByRole("button", { name: "Write me an email" });
      const icon = example.querySelector(".svg-color");
      expect(icon).not.toBeNull();
      expect(contrastOnPaper(icon, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });
  },
);
