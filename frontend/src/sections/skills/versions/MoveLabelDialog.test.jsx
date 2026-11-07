import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import MoveLabelDialog from "./MoveLabelDialog";

const mocks = vi.hoisted(() => ({
  labels: vi.fn(),
  compare: vi.fn(),
  move: vi.fn(),
  refetch: vi.fn(),
  toast: vi.fn(),
}));
vi.mock("src/api/skills", () => ({
  useSkillLabels: mocks.labels,
  useSkill: () => ({ data: { ...skill, labels: mocks.labels().data }, refetch: async () => { const response = await mocks.refetch(); return { ...response, data: response.data ? { ...skill, labels: response.data } : undefined }; } }),
  useSkillVersion: () => ({ data: { digest: 'sha256:target' } }),
  useSkillCompare: mocks.compare,
  useSetSkillLabel: () => ({ mutateAsync: mocks.move, isPending: false }),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.toast }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/skills/components/VersionSelect", () => {
  function VersionSelect({ value, onChange }) {
    return (
      <select
        aria-label="Version"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {[1, 2, 3].map((number) => (
          <option key={number} value={number}>
            v{number}
          </option>
        ))}
      </select>
    );
  }
  VersionSelect.propTypes = {
    value: PropTypes.number,
    onChange: PropTypes.func,
  };
  return { default: VersionSelect };
});

const skill = { id: "skill-1", latestVersion: 3, releaseRevision: 4, protectedLabelNames: ["production"], permissions: { canManageLabelProtection: true, canRunTests: true } };
const labelsResult = (version = 1) => ({
  data: [{ name: "production", version, canMove: true }],
  refetch: mocks.refetch,
});
const comparison = {
  summary: { added: 1, removed: 0, modified: 2, unchanged: 1 },
};

beforeEach(() => {
  vi.resetAllMocks();
  mocks.labels.mockReturnValue(labelsResult());
  mocks.compare.mockReturnValue({ data: comparison });
  mocks.move.mockResolvedValue({});
  mocks.refetch.mockResolvedValue(labelsResult(2));
});

describe("MoveLabelDialog", () => {
  it("shows the reviewed file summary and sends its current label version for CAS", async () => {
    const onClose = vi.fn();
    render(
      <MoveLabelDialog
        open
        onClose={onClose}
        skill={skill}
        label="production"
      />,
    );
    expect(screen.getByText("Currently points to v1.")).toBeInTheDocument();
    expect(screen.getByText("Modified")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    );
    await waitFor(() =>
      expect(mocks.move).toHaveBeenCalledWith({
        label: "production",
        version: 3,
        expectedVersion: 1, expectedReleaseRevision: 4, expectedDigest: "sha256:target", evidenceRunId: null,
        note: "",
      }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not silently adopt a background-refetched pointer", async () => {
    const { rerender } = render(
      <MoveLabelDialog
        open
        onClose={vi.fn()}
        skill={skill}
        label="production"
      />,
    );
    mocks.labels.mockReturnValue(labelsResult(2));
    rerender(
      <MoveLabelDialog
        open
        onClose={vi.fn()}
        skill={skill}
        label="production"
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    );
    await waitFor(() =>
      expect(mocks.move).toHaveBeenCalledWith(
        expect.objectContaining({ expectedVersion: 1 }),
      ),
    );
  });

  it("requires an explicit reload and review after a CAS conflict", async () => {
    mocks.move
      .mockRejectedValueOnce({ code: "label_conflict" })
      .mockResolvedValueOnce({});
    const onClose = vi.fn();
    render(
      <MoveLabelDialog
        open
        onClose={onClose}
        skill={skill}
        label="production"
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    );
    expect(
      await screen.findByText(/This label changed while you were reviewing it/),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Reload current label" }),
    );
    await screen.findByText("Currently points to v2.");
    expect(mocks.compare).toHaveBeenLastCalledWith(skill.id, 2, 3);
    fireEvent.click(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    );
    await waitFor(() =>
      expect(mocks.move).toHaveBeenLastCalledWith(
        expect.objectContaining({ expectedVersion: 2 }),
      ),
    );
  });

  it("keeps a conflict blocked when reloading fails", async () => {
    mocks.move.mockRejectedValueOnce({ code: "label_conflict" });
    mocks.refetch.mockRejectedValueOnce(new Error("Connection lost"));
    render(
      <MoveLabelDialog
        open
        onClose={vi.fn()}
        skill={skill}
        label="production"
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Reload current label" }),
    );
    expect(
      await screen.findByText(/Could not reload the current label/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    ).toBeDisabled();
  });

  it("uses must-not-exist CAS for new labels and rejects the virtual latest label", async () => {
    render(<MoveLabelDialog open onClose={vi.fn()} skill={skill} />);
    fireEvent.change(screen.getByLabelText("Label"), {
      target: { value: "latest" },
    });
    expect(
      screen.getByRole("button", { name: "Move latest from unassigned to v3" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Label"), {
      target: { value: "staging" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move staging from unassigned to v3" }));
    await waitFor(() =>
      expect(mocks.move).toHaveBeenCalledWith(
        expect.objectContaining({ label: "staging", expectedVersion: null }),
      ),
    );
  });

  it("enforces the label limit without blocking a move of an existing label", () => {
    mocks.labels.mockReturnValue({
      data: Array.from({ length: 20 }, (_, index) => ({
        name: `release-${index}`,
        version: 1, canMove: true,
      })),
    });
    render(<MoveLabelDialog open onClose={vi.fn()} skill={skill} />);
    expect(screen.getByText(/already has 20 labels/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Label"), {
      target: { value: "release-0" },
    });
    expect(
      screen.getByRole("button", { name: "Move release-0 from v1 to v3" }),
    ).toBeEnabled();
  });

  it("blocks promotion until the change summary is available", () => {
    mocks.compare.mockReturnValue({ isError: true, refetch: vi.fn() });
    render(
      <MoveLabelDialog
        open
        onClose={vi.fn()}
        skill={skill}
        label="production"
      />,
    );
    expect(
      screen.getByText("Could not compare these versions"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Move production from .* to v3/ }),
    ).toBeDisabled();
  });
});

vi.mock("src/sections/skills/tests/TestEvidence", () => ({ default: () => <div>Test evidence</div> }));
