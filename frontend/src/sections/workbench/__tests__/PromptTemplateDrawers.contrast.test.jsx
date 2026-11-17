import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ChoosePromptTemplateDrawer } from "../ChoosePromptTemplateDrawer";
import { SelectedPromptTemplateDrawer } from "../SelectedPromptTemplateDrawer";
import { AA_TEXT, PROMPT_THEMES, contrastOnPaper } from "./promptsContrast";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  },
  endpoints: {
    develop: {
      runPrompt: {
        promptTemplate: "/prompt-base-templates/",
        categories: "/prompt-base-templates/categories/",
        createPromptDraft: "/prompt-templates/create-draft/",
      },
    },
  },
}));
vi.mock("src/sections/develop-detail/RunPrompt/Modals/ImportPrompt", () => ({
  ReadOnlyPrompt: () => <div data-testid="read-only-message" />,
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

function withProviders(node, theme) {
  return (
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

const template = {
  id: "tpl-travel",
  name: "Travel planner",
  desc: "",
  promptConfig: {
    id: "tpl-travel",
    name: "Travel planner",
    category: "planning",
    prompt_config_snapshot: {
      messages: [{ role: "user", content: "Plan a trip to {{city}}" }],
    },
  },
};

// Back (an enabled button) and the Categories heading used
// text.disabled, so they looked disabled and failed contrast.
describe.each(PROMPT_THEMES)(
  "Readable template drawers (%s theme)",
  (_mode, theme) => {
    it("paints Back in the template preview in a readable colour", () => {
      render(
        withProviders(
          <SelectedPromptTemplateDrawer
            open
            onClose={vi.fn()}
            data={template}
          />,
          theme,
        ),
      );
      const back = screen.getByRole("button", { name: "Back" });
      expect(back).toBeEnabled();
      expect(contrastOnPaper(back, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it("shows the Categories heading of Prompt Templates in a readable colour", () => {
      render(
        withProviders(
          <ChoosePromptTemplateDrawer open onClose={vi.fn()} />,
          theme,
        ),
      );
      expect(
        contrastOnPaper(screen.getByText("Categories"), theme),
      ).toBeGreaterThanOrEqual(AA_TEXT);
    });
  },
);

// Every control has an accessible name: the icon-only close buttons say so.
describe("Template drawers close buttons", () => {
  it("names the close button of the template preview and of Prompt Templates", () => {
    const light = PROMPT_THEMES[0][1];
    const { unmount } = render(
      withProviders(
        <SelectedPromptTemplateDrawer open onClose={vi.fn()} data={template} />,
        light,
      ),
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    unmount();
    render(
      withProviders(
        <ChoosePromptTemplateDrawer open onClose={vi.fn()} />,
        light,
      ),
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });
});
