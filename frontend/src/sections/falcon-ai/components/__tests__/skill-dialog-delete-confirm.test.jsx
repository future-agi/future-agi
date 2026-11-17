import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "src/utils/test-utils";

import SkillEditorDialog from "../SkillEditorDialog";

// The Edit Skill window (under the empty chat and in the header Skills menu)
// reaches the server only through these calls.
const mocks = vi.hoisted(() => ({
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
}));

vi.mock("../../hooks/useFalconAPI", () => mocks);

const SKILL = {
  id: "skill-1",
  name: "Bug triage",
  description: "",
  icon: "mdi:bug-outline",
  instructions: "Group new errors by their likely cause.",
  trigger_phrases: ["triage errors"],
};

// The same confirm, in the same words, as Customize asks before it deletes a
// skill.
const CONFIRM_TITLE = "Delete skill?";
const CONFIRM_TEXT =
  "Bug triage and its instructions are removed, and Falcon stops using it. You can't undo this.";

const NETWORK_DROP = { message: "Network Error", transportCode: "ERR_NETWORK" };

const openEditor = () => {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(
    <SkillEditorDialog
      open
      skill={SKILL}
      onClose={onClose}
      onSaved={onSaved}
    />,
  );
  return { onClose, onSaved };
};

const confirmDialog = () =>
  screen.findByRole("dialog", { name: CONFIRM_TITLE });

const confirmGone = () =>
  waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: CONFIRM_TITLE }),
    ).not.toBeInTheDocument(),
  );

beforeEach(() => {
  vi.clearAllMocks();
  mocks.deleteSkill.mockResolvedValue(undefined);
});

describe("Edit Skill window > Delete", () => {
  it("asks before deleting, and Cancel keeps the skill", async () => {
    const { onClose, onSaved } = openEditor();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    // One click used to remove the skill on the spot.
    const dialog = await confirmDialog();
    expect(dialog).toHaveTextContent(CONFIRM_TEXT);
    expect(mocks.deleteSkill).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await confirmGone();
    expect(mocks.deleteSkill).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Edit Skill" }),
    ).toBeInTheDocument();
  });

  it("deletes once confirmed and closes the window", async () => {
    const { onClose, onSaved } = openEditor();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await confirmDialog();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete skill" }),
    );

    await waitFor(() =>
      expect(mocks.deleteSkill).toHaveBeenCalledWith(SKILL.id),
    );
    expect(mocks.deleteSkill).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onSaved).toHaveBeenCalledTimes(1);
    await confirmGone();
  });

  it("says the delete failed and keeps the skill open", async () => {
    mocks.deleteSkill.mockRejectedValue(NETWORK_DROP);
    const { onClose, onSaved } = openEditor();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await confirmDialog();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete skill" }),
    );

    await confirmGone();
    expect(
      await screen.findByText("Failed to delete skill."),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Edit Skill" }),
    ).toBeInTheDocument();
  });
});
