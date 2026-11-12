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
import { PROMPT_ITEM_TYPES } from "../../common";
import PromptItem from "../PromptItem";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../RenameItem", () => ({ RenameItem: () => null }));
vi.mock("../DeleteItem", () => ({ DeleteItem: () => null }));
vi.mock("../MoveItem", () => ({ default: () => null }));

function renderItem(theme) {
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <PromptItem
          type={PROMPT_ITEM_TYPES.FILE}
          id="p1"
          name="Support reply"
          createdBy="Ana"
          lastModified="2026-11-10T12:00:00Z"
          extraData={{
            prompt_folder: "f1",
            prompt_folderName: "Marketing",
            collaborators: [],
          }}
          isSearching
        />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

// The row's More button and the "All Prompts" part of a
// search result's path used text.disabled, so they looked disabled.
describe.each(PROMPT_THEMES)(
  "Readable prompt list row (%s theme)",
  (_mode, theme) => {
    it("paints the More actions button in a readable colour", () => {
      renderItem(theme);
      const more = screen.getByRole("button", {
        name: "More actions for Support reply",
      });
      expect(more).toBeEnabled();
      const icon = more.querySelector(".svg-color");
      expect(icon).not.toBeNull();
      expect(contrastOnPaper(icon, theme)).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it("shows a search result's folder path in readable colours", () => {
      renderItem(theme);
      expect(
        contrastOnPaper(screen.getByText("All Prompts"), theme),
      ).toBeGreaterThanOrEqual(AA_TEXT);
      expect(
        contrastOnPaper(screen.getByText("Marketing"), theme),
      ).toBeGreaterThanOrEqual(AA_TEXT);
    });

    it("shows who created the prompt and when it changed in a readable colour", () => {
      renderItem(theme);
      expect(
        contrastOnPaper(screen.getByText(/Created by Ana/), theme),
      ).toBeGreaterThanOrEqual(AA_TEXT);
    });
  },
);
