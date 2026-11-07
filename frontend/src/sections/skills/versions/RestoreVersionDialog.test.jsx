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
