import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import RestoreVersionDialog from "./RestoreVersionDialog";

const mocks = vi.hoisted(() => ({
  restore: vi.fn(),
  toast: vi.fn(),
  pending: false,
}));
vi.mock("src/api/skills", () => ({
  useRestoreSkillVersion: () => ({
    mutateAsync: mocks.restore,
    isPending: mocks.pending,
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.toast }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/skills/governance/LabelProtectionDialog", async () => {
  // A portal on top, like the real nested dialog, so the open outer dialog does not hide it.
  const { createPortal } = await import("react-dom");
  // eslint-disable-next-line react/prop-types
  return { default: ({ open }) => (open ? createPortal(<div role="dialog" aria-label="Manage label protection">Label rules</div>, document.body) : null) };
});
const skill = { id: "skill-1", name: "release-review" };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.pending = false;
  mocks.restore.mockResolvedValue({ version: { number: 4 }, unchanged: false });
});

describe("RestoreVersionDialog", () => {
  it("only restores after confirmation and explains that labels stay unchanged", async () => {
    const onClose = vi.fn();
    render(<RestoreVersionDialog skill={skill} number={1} onClose={onClose} />);
    expect(
      screen.getByText(/Existing versions and labels will not change/),
    ).toBeInTheDocument();
    expect(mocks.restore).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore as new version" }),
    );
    await waitFor(() =>
      expect(mocks.restore).toHaveBeenCalledWith({ number: 1 }),
    );
    expect(mocks.toast).toHaveBeenCalledWith(
      "Restored as v4. Labels have not moved.",
      { variant: "success" },
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not claim a new version for an unchanged restore", async () => {
    mocks.restore.mockResolvedValue({
      version: { number: 3 },
      unchanged: true,
    });
    render(<RestoreVersionDialog skill={skill} number={3} onClose={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Restore as new version" }),
    );
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.stringContaining("No new version was created"),
        { variant: "success" },
      ),
    );
  });

  it("keeps the dialog open on failure and permits retry", async () => {
    mocks.restore.mockRejectedValueOnce(new Error("Unavailable"));
    const onClose = vi.fn();
    render(<RestoreVersionDialog skill={skill} number={1} onClose={onClose} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Restore as new version" }),
    );
    expect(
      await screen.findByText(/Could not restore this version/),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore as new version" }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });

  it("does not mutate when canceled and disables confirmation while pending", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <RestoreVersionDialog skill={skill} number={1} onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(mocks.restore).not.toHaveBeenCalled();
    mocks.pending = true;
    rerender(
      <RestoreVersionDialog skill={skill} number={1} onClose={onClose} />,
    );
    expect(screen.getByRole("button", { name: "Restoring…" })).toBeDisabled();
  });
});

describe("UXF-09: the label rule in the restore dialog", () => {
  it("states who may move the protected labels afterwards, from the skill's rules", () => {
    render(<RestoreVersionDialog skill={{ ...skill, protectedLabelNames: ["production"], permissions: { canManageLabelProtection: false } }} number={1} onClose={vi.fn()} />);
    expect(screen.getByText(/Existing versions and labels will not change/)).toBeInTheDocument();
    expect(screen.getByText("Production is a protected label: only workspace admins can move it, and automation can't.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change rules" })).not.toBeInTheDocument();
    expect(document.body.textContent.replace(/protected labels?/gi, "").match(/protected/i)).toBeNull();
  });

  it("lets an admin open the rules without restoring", () => {
    render(<RestoreVersionDialog skill={{ ...skill, protectedLabelNames: ["production", "staging"], permissions: { canManageLabelProtection: true } }} number={1} onClose={vi.fn()} />);
    expect(screen.getByText("Protected labels (only workspace admins can move them, and automation can't): production, staging.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change rules" }));
    expect(screen.getByRole("dialog", { name: "Manage label protection" })).toBeInTheDocument();
    expect(mocks.restore).not.toHaveBeenCalled();
  });

  it("claims no rule when the skill's rules are not loaded", () => {
    render(<RestoreVersionDialog skill={skill} number={1} onClose={vi.fn()} />);
    expect(screen.queryByTestId("release-rule-note")).not.toBeInTheDocument();
  });

  it("keeps the explanation beside a failure", async () => {
    mocks.restore.mockRejectedValueOnce(new Error("Unavailable"));
    render(<RestoreVersionDialog skill={{ ...skill, protectedLabelNames: [] }} number={2} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Restore as new version" }));
    expect(await screen.findByText(/Could not restore this version/)).toBeInTheDocument();
    expect(screen.getByText(/Publish the files from v2 as a new version of release-review/)).toBeInTheDocument();
    expect(screen.getByText("No labels are protected.")).toBeInTheDocument();
  });
});
