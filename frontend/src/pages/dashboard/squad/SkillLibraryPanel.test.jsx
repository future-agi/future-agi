import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor, act } from "src/utils/test-utils";
import SkillLibraryPanel from "./SkillLibraryPanel";

const skill = {
  id: "skill_00000000-0000-4000-8000-000000000001",
  revision: 2,
  name: "Evidence brief",
  description: "Explain evidence.",
  instructions: "Cite each source.",
  status: "active",
  digest: `sha256:${"a".repeat(64)}`,
};
const envelope = (skills = [skill], canManage = true) => ({
  available: true,
  schemaVersion: "omega-workspace-skills/v1",
  skills,
  canManage,
});
function client() {
  return {
    listWorkspaceSkills: vi.fn().mockResolvedValue(envelope()),
    getWorkspaceSkill: vi.fn().mockResolvedValue({
      available: true,
      schemaVersion: "omega-workspace-skills/v1",
      skill,
      canManage: true,
    }),
    createWorkspaceSkill: vi.fn().mockResolvedValue({
      available: true,
      schemaVersion: "omega-workspace-skills/v1",
      skill,
    }),
    updateWorkspaceSkill: vi.fn().mockResolvedValue({
      available: true,
      schemaVersion: "omega-workspace-skills/v1",
      skill: { ...skill, revision: 3 },
    }),
    archiveWorkspaceSkill: vi.fn().mockResolvedValue({
      available: true,
      schemaVersion: "omega-workspace-skills/v1",
      skill: { ...skill, status: "archived" },
    }),
  };
}
describe("Workspace skill library", () => {
  it("captures a complete revision before editing and preserves the draft on version conflict", async () => {
    const api = client();
    api.updateWorkspaceSkill.mockResolvedValue({
      available: false,
      reason: "Skill revision changed",
    });
    render(<SkillLibraryPanel client={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Read skill" }),
    );
    const instructions = await screen.findByRole("textbox", {
      name: "Skill instructions",
    });
    await userEvent.clear(instructions);
    await userEvent.type(instructions, "Edited instructions");
    await userEvent.click(
      screen.getByRole("button", { name: "Save new revision" }),
    );
    expect(api.updateWorkspaceSkill).toHaveBeenCalledWith(skill.id, {
      name: skill.name,
      description: skill.description,
      instructions: "Edited instructions",
      expectedRevision: 2,
    });
    expect(
      await screen.findByText("Skill revision changed"),
    ).toBeInTheDocument();
    expect(instructions).toHaveValue("Edited instructions");
  });

  it("reuses the same creation key after a lost response and never auto-attaches a skill", async () => {
    const api = client();
    const saved = {
      available: true,
      schemaVersion: "omega-workspace-skills/v1",
      skill: {
        ...skill,
        name: "Source review",
        description: "Review sources",
        instructions: "Record evidence and unknowns.",
      },
    };
    api.createWorkspaceSkill
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(saved);
    api.getWorkspaceSkill.mockResolvedValue(saved);
    render(<SkillLibraryPanel client={api} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Create skill" }),
      ).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Create skill" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Skill name" }),
      "Source review",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Skill description" }),
      "Review sources",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Skill instructions" }),
      "Record evidence and unknowns.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save skill" }));
    expect(
      await screen.findByText(/Your draft is kept here; retry safely/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save skill" }));
    expect(api.createWorkspaceSkill).toHaveBeenCalledTimes(2);
    expect(api.createWorkspaceSkill.mock.calls[0]).toEqual(
      api.createWorkspaceSkill.mock.calls[1],
    );
    expect(
      await screen.findByText(/Select its revision on an agent to use it/),
    ).toBeInTheDocument();
  });

  it("lets members read complete instructions without exposing mutation controls", async () => {
    const api = client();
    api.listWorkspaceSkills.mockResolvedValue(envelope([skill], false));
    api.getWorkspaceSkill.mockResolvedValue({
      available: true,
      schemaVersion: "omega-workspace-skills/v1",
      skill,
      canManage: false,
    });
    render(<SkillLibraryPanel client={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Read skill" }),
    );
    expect(
      await screen.findByRole("textbox", { name: "Skill instructions" }),
    ).toHaveValue(skill.instructions);
    expect(
      screen.getByRole("textbox", { name: "Skill instructions" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Save new revision" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive skill" }),
    ).not.toBeInTheDocument();
  });

  it("clears a previous workspace and ignores a late detail read", async () => {
    const first = client();
    let resolve;
    first.getWorkspaceSkill.mockImplementation(
      () =>
        new Promise((accept) => {
          resolve = accept;
        }),
    );
    const view = render(<SkillLibraryPanel client={first} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Read skill" }),
    );
    const second = client();
    second.listWorkspaceSkills.mockResolvedValue(envelope([], false));
    view.rerender(<SkillLibraryPanel client={second} />);
    expect(
      await screen.findByText("No workspace skills yet."),
    ).toBeInTheDocument();
    await act(async () =>
      resolve({
        available: true,
        schemaVersion: "omega-workspace-skills/v1",
        skill,
        canManage: true,
      }),
    );
    expect(
      screen.queryByRole("textbox", { name: "Skill instructions" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create skill" })).toBeDisabled();
  });

  it("archives only the captured revision after explicit user action", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      const api = client();
      api.getWorkspaceSkill
        .mockResolvedValueOnce({
          available: true,
          schemaVersion: "omega-workspace-skills/v1",
          skill,
          canManage: true,
        })
        .mockResolvedValue({
          available: true,
          schemaVersion: "omega-workspace-skills/v1",
          skill: { ...skill, status: "archived" },
          canManage: true,
        });
      render(<SkillLibraryPanel client={api} />);
      await userEvent.click(
        await screen.findByRole("button", { name: "Read skill" }),
      );
      await userEvent.click(
        await screen.findByRole("button", { name: "Archive skill" }),
      );
      expect(api.archiveWorkspaceSkill).toHaveBeenCalledWith(skill.id, {
        expectedRevision: 2,
      });
      expect(
        await screen.findByText(/Historical revisions remain readable/),
      ).toBeInTheDocument();
    } finally {
      confirm.mockRestore();
    }
  });
});
