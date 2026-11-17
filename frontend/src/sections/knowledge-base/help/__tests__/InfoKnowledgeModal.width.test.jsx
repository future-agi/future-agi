// Knowledge base "View Docs" help dialog. With maxWidth={false} and its
// width set in PaperProps outside sx (so never applied as CSS), the dialog
// filled the window; the demo frame grows with the dialog's width and pushed
// Create Knowledge Base below the fold. The dialog keeps a bounded width,
// with Create inside it.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import InfoKnowledgeModal from "../InfoKnowledgeModal";

vi.mock("src/components/iconify", () => ({ default: () => null }));

// The widest the help dialog may be (the design-system pass meant 720px).
const MAX_DIALOG_PX = 720;
const OWNER = { user: {}, authenticated: true, role: "Owner" };

function renderHelp({ onCreateKnowledge = vi.fn() } = {}) {
  render(
    <AuthContext.Provider value={OWNER}>
      <InfoKnowledgeModal
        open
        onClose={vi.fn()}
        onCreateKnowledge={onCreateKnowledge}
      />
    </AuthContext.Provider>,
  );
  return screen.getByRole("dialog");
}

describe("Knowledge base help dialog", () => {
  afterEach(() => {
    cleanup();
  });

  it("keeps a bounded width instead of filling the window", () => {
    const dialog = renderHelp();

    expect(dialog).not.toHaveClass("MuiDialog-paperWidthFalse");
    const maxWidth = getComputedStyle(dialog).maxWidth;
    expect(maxWidth).toMatch(/^\d+(\.\d+)?px$/);
    expect(parseFloat(maxWidth)).toBeLessThanOrEqual(MAX_DIALOG_PX);
  });

  it("shows Create Knowledge Base inside the dialog", () => {
    const onCreateKnowledge = vi.fn();
    const dialog = renderHelp({ onCreateKnowledge });

    const create = within(dialog).getByRole("button", {
      name: "Create Knowledge Base",
    });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    expect(onCreateKnowledge).toHaveBeenCalledTimes(1);
  });
});
