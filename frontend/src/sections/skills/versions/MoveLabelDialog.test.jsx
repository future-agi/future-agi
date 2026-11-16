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
  evidence: vi.fn(),
}));
vi.mock("src/api/skills", () => ({
  useSkillLabels: mocks.labels,
  useSkillVersions: () => ({ data: { results: [{ number: 3 }, { number: 2 }, { number: 1 }] } }),
  useSkill: () => ({ data: { ...skill, labels: mocks.labels().data }, refetch: async () => { const response = await mocks.refetch(); return { ...response, data: response.data ? { ...skill, labels: response.data } : undefined }; } }),
  useSkillVersion: () => ({ data: { digest: 'sha256:target' } }),
  useSkillCompare: mocks.compare,
  useSetSkillLabel: () => ({ mutateAsync: mocks.move, isPending: false }),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("src/api/skills/testing", () => ({ useSkillTestEvidence: () => mocks.evidence() }));
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
  mocks.evidence.mockReturnValue({ data: { runs: [{ runId: "run-1" }] } });
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
    expect(screen.getByText("2 files modified")).toBeInTheDocument();
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

// eslint-disable-next-line react/prop-types
vi.mock("src/sections/skills/tests/TestEvidence", () => ({ default: ({ onSelectRun }) => <div>Test evidence<button type="button" onClick={() => onSelectRun("run-1")}>Attach mocked evidence</button></div> }));

it('defaults to the newest different version and explains an explicit no-op', async () => {
  mocks.labels.mockReturnValue(labelsResult(3));
  render(<MoveLabelDialog open onClose={vi.fn()} skill={skill} label="production" />);
  expect(screen.getByRole('combobox', { name: 'Version' })).toHaveValue('2');
  fireEvent.change(screen.getByRole('combobox', { name: 'Version' }), { target: { value: '3' } });
  expect(screen.getByText('Production already points to v3.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Move production', exact: true })).toBeDisabled();
  expect(screen.getByText('Choose a different version')).toBeInTheDocument();
  expect(mocks.move).not.toHaveBeenCalled();
});

describe("moving production states its consequence at the point of decision", () => {
  const open = (label = "production") => render(<MoveLabelDialog open onClose={vi.fn()} skill={skill} label={label} />);
  it("says what happens to apps and pinned versions, and keeps the single click", async () => {
    open();
    expect(await screen.findByText("Apps that follow production pick up v3 the next time they fetch. Pinned versions don't change.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Version" }), { target: { value: "2" } });
    expect(screen.getByText(/pick up v2 the next time they fetch/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Move production from v1 to v2$/ }));
    await waitFor(() => expect(mocks.move).toHaveBeenCalledTimes(1));
  });
  it("shows the missing-evidence line once, as a warning beside the action, and Test evidence only when runs exist", async () => {
    mocks.evidence.mockReturnValue({ data: { runs: [] } });
    open();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No tests run for these files. Not tested is not a pass.");
    expect(screen.getAllByText(/Not tested is not a pass/)).toHaveLength(1);
    expect(screen.queryByText("Test evidence")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Run tests" })).toHaveAttribute("href", "/dashboard/skills/skill-1/edit?tab=tests");
  });
  it("keeps Test evidence (and its attach control) when the version has runs", async () => {
    open();
    expect(await screen.findByText("Test evidence")).toBeInTheDocument();
    expect(screen.queryByText(/Not tested is not a pass/)).not.toBeInTheDocument();
  });
  it("says beside the unchanged move action that attached evidence is saved with the move", async () => {
    open();
    await screen.findByText("Test evidence");
    expect(screen.queryByText(/saves the attached test evidence/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Attach mocked evidence" }));
    expect(screen.getByText("Moving saves the attached test evidence with this label change.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Move production from v1 to v3$/ }));
    await waitFor(() => expect(mocks.move).toHaveBeenCalledTimes(1));
    expect(mocks.move.mock.calls[0][0]).toMatchObject({ evidenceRunId: "run-1", expectedVersion: 1 });
  });
  it("does not talk about production for another label", async () => {
    mocks.labels.mockReturnValue({ data: [{ name: "staging", version: 1, canMove: true }, { name: "production", version: 2, canMove: true }], refetch: mocks.refetch });
    open("staging");
    await screen.findByText("Currently points to v1.");
    expect(screen.queryByText(/Apps that follow production/)).not.toBeInTheDocument();
  });
});
