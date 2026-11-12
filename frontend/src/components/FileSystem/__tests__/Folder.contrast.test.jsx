import React from "react";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  AA_TEXT,
  PROMPT_THEMES,
  contrastOnPaper,
} from "src/sections/workbench/__tests__/promptsContrast";
import Folder from "../Folder";

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/sections/workbench-v2/components/DeleteItem", () => ({
  DeleteItem: () => null,
}));
vi.mock("src/sections/workbench-v2/components/RenameItem", () => ({
  RenameItem: () => null,
}));

// A folder's More actions button in the Prompts folder rail
// used text.disabled, so an enabled control looked disabled.
describe.each(PROMPT_THEMES)(
  "Readable folder rail (%s theme)",
  (_mode, theme) => {
    it("paints a folder's More actions button in a readable colour", () => {
      render(
        <ThemeProvider theme={theme}>
          <MemoryRouter>
            <Folder
              label="Marketing"
              id="f1"
              type="FOLDER"
              isChildren
              onClick={vi.fn()}
              onToggle={vi.fn()}
            />
          </MemoryRouter>
        </ThemeProvider>,
      );
      const more = screen.getByRole("button", {
        name: "More actions for Marketing",
      });
      expect(more).toBeEnabled();
      const icon = more.querySelector(".svg-color");
      expect(icon).not.toBeNull();
      expect(contrastOnPaper(icon, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });
  },
);
