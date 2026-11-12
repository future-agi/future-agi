import { createTheme } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MoreActions from "../MoreActions";

const props = (isMoreOpen) => ({
  isMoreOpen,
  setMoreOpen: vi.fn(),
  loadingPrompt: false,
  currentTab: "Playground",
  setVersionHistoryOpen: vi.fn(),
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
  id: "prompt-1",
  selectedVersions: [{ version: "V1" }],
  theme: createTheme(),
  isAddingDraft: false,
  addToCompare: vi.fn(),
  userRole: "Owner",
});

// FA-PROMPTS-07: below 1440 px the More button is icon-only (the default
// matchMedia mock reports a narrow screen); it still has a plain name.
describe("MoreActions toggle name", () => {
  it('is named "More actions" when closed', () => {
    render(<MoreActions {...props(false)} />);
    expect(
      screen.getByRole("button", { name: "More actions" }),
    ).toBeInTheDocument();
  });

  it('is named "Fewer actions" when open', () => {
    render(<MoreActions {...props(true)} />);
    expect(
      screen.getByRole("button", { name: "Fewer actions" }),
    ).toBeInTheDocument();
  });
});
