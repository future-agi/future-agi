import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { isZero } from "src/sections/workbench/__tests__/promptsContrast";
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

const LONG_NAME =
  "Customer support escalation reply for enterprise accounts in the EU region";

function renderRow(type) {
  render(
    <MemoryRouter>
      <PromptItem
        type={type}
        id="p1"
        name={LONG_NAME}
        createdBy="someone.with.a.long.address@example.com"
        lastModified="2026-11-10T12:00:00Z"
        extraData={{ collaborators: [] }}
      />
    </MemoryRouter>,
  );
}

// A prompt or folder row
// links its name and "Created by ... Last modified ..." line. The link kept
// its content width (a flex item's min-width: auto), so a long line pushed
// the row's More actions button past the right edge at 1024 px and on a
// phone. The link has to be able to shrink (the text ends in an ellipsis).
describe.each([
  ["prompt", PROMPT_ITEM_TYPES.FILE],
  ["folder", PROMPT_ITEM_TYPES.FOLDER],
])("%s row keeps More actions on screen", (_label, type) => {
  it("lets the name link shrink so the More button stays on the row", () => {
    renderRow(type);
    const more = screen.getByRole("button", {
      name: `More actions for ${LONG_NAME}`,
    });
    const link = screen.getByRole("link");
    expect(link).toHaveTextContent(LONG_NAME);
    // The link and the button share one row.
    expect(link.parentElement).toBe(more.parentElement);
    expect(isZero(getComputedStyle(link).minWidth)).toBe(true);
  });
});
