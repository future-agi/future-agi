import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// What the server answered for the versions read, and what each action got.
const state = { versions: undefined, canUnlockRelease: true, canEdit: true };
const calls = { lock: [], unlock: [] };

vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  return {
    ...actual,
    useEnvironmentVersions: vi.fn(() => ({
      data: state.versions ? actual.toVersions(state.versions) : undefined,
    })),
    useLockRelease: vi.fn(() => ({
      isPending: false,
      mutate: (variables) => calls.lock.push(variables),
    })),
    useUnlockRelease: vi.fn(() => ({
      isPending: false,
      mutate: (variables) => calls.unlock.push(variables),
    })),
  };
});
vi.mock("../../../hooks/useSimulationAccess", () => ({
  default: () => ({
    canEdit: state.canEdit,
    canRun: true,
    canUnlockRelease: state.canUnlockRelease,
  }),
}));

const { default: ReleaseCard } = await import("../ReleaseCard");
const { versionLine } = await import("../overview.constants");

const piece = (id, label) => ({ id, label, status: "active", is_default: true });
const RELEASE = {
  id: "rel-1",
  label: "2026.11.1",
  environment_version_id: "env-3",
  environment_label: "v3",
  suite_version_id: "suite-5",
  suite_label: "v5",
  locked_at: "2026-11-25T08:00:00Z",
  locked_by: { id: "u", name: "Kartik" },
  unlocked_at: null,
  unlocked_by: null,
  train: 54,
  test: 14,
};
const answer = ({ releases = [], lock = {} } = {}) => ({
  agent: [
    {
      ...piece("agent-4", "v4"),
      source: {
        repository: "acme/support-bot",
        ref: "main",
        commit_sha: "a1b2c3d4e5f6",
      },
    },
  ],
  environment: [piece("env-3", "v3")],
  suite: [piece("suite-5", "v5")],
  releases,
  lock: {
    environment_version_id: "env-3",
    suite_version_id: "suite-5",
    proved: false,
    can_lock: true,
    refusal: null,
    train: 54,
    test: 14,
    test_target: 14,
    ...lock,
  },
});

beforeEach(() => {
  state.versions = answer();
  state.canUnlockRelease = true;
  state.canEdit = true;
  calls.lock.length = 0;
  calls.unlock.length = 0;
});

describe("ReleaseCard", () => {
  it("states the version line", () => {
    render(<ReleaseCard envId="env" />);

    expect(
      screen.getByText(
        "agent v4 · acme/support-bot@main · a1b2c3d · environment v3 · scenarios v5",
      ),
    ).toBeInTheDocument();
  });

  it("offers the lock with the splits it would get", async () => {
    render(<ReleaseCard envId="env" />);

    expect(screen.getByText("Release")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Not proved")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Train 54 · Test 14 tasks once locked. Lock to freeze tasks, splits and the scorer.",
      ),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Lock this release" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Lock this release?")).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lock release" }),
    );

    expect(calls.lock).toEqual([
      { environmentVersionId: "env-3", suiteVersionId: "suite-5" },
    ]);
  });

  it("says why it can't lock, in one sentence, with the fix", async () => {
    state.versions = answer({
      lock: {
        can_lock: false,
        refusal: {
          code: "release_too_few_test",
          message: "Add scenarios to lock: a test split needs at least 4.",
        },
        train: 7,
        test: 3,
      },
    });

    const onGo = vi.fn();
    render(<ReleaseCard envId="env" onGo={onGo} />);

    expect(screen.getByText("Can't lock yet")).toBeInTheDocument();
    expect(
      screen.getByText("Add scenarios to lock: a test split needs at least 4."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Lock this release" }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add scenarios" }));
    expect(onGo).toHaveBeenCalledWith("scenarios");
  });

  it("offers no action when the build, not the suite, is the reason", () => {
    state.versions = answer({
      lock: {
        can_lock: false,
        refusal: {
          code: "release_not_ready",
          message:
            "This environment is building or its last build failed. Lock it once a build finishes.",
        },
      },
    });

    render(<ReleaseCard envId="env" onGo={vi.fn()} />);

    expect(
      screen.queryByRole("button", { name: "Add scenarios" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the lock from someone who can't edit", () => {
    state.canEdit = false;

    render(<ReleaseCard envId="env" />);

    expect(
      screen.getByRole("button", { name: "Lock this release" }),
    ).toBeDisabled();
  });

  it("shows a locked release with its splits, and an admin can unlock it", async () => {
    state.versions = answer({
      releases: [RELEASE],
      lock: { can_lock: false, proved: true },
    });

    render(<ReleaseCard envId="env" />);

    expect(screen.getByText("Release 2026.11.1")).toBeInTheDocument();
    expect(screen.getByText("Locked")).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(
      screen.getByText(/Train 54 · Test 14 tasks · scorer environment v3\./),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Lock this release" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Unlock release 2026.11.1?"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Its scores stay, but they're no longer a locked comparison. The train/test split of each task never changes.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Unlock release" }),
    );

    expect(calls.unlock).toEqual([{ releaseId: "rel-1" }]);
  });

  it("hides Unlock from a member", () => {
    state.canUnlockRelease = false;
    state.versions = answer({ releases: [RELEASE], lock: { can_lock: false } });

    render(<ReleaseCard envId="env" />);

    expect(
      screen.queryByRole("button", { name: "Unlock" }),
    ).not.toBeInTheDocument();
  });

  it("lets an admin unlock an earlier release the default moved past", async () => {
    state.versions = answer({
      releases: [
        { ...RELEASE, suite_version_id: "suite-4", suite_label: "v4" },
      ],
    });

    render(<ReleaseCard envId="env" />);

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2026.11.1 · environment v3 · scenarios v4 · Train 54 · Test 14 · locked",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Unlock release 2026.11.1?"),
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Unlock release" }),
    );

    expect(calls.unlock).toEqual([{ releaseId: "rel-1" }]);
  });

  it("lists earlier releases, unlocked ones included", () => {
    state.versions = answer({
      releases: [
        {
          ...RELEASE,
          id: "rel-0",
          label: "2026.11.1",
          suite_version_id: "suite-4",
          suite_label: "v4",
          unlocked_at: "2026-11-26T08:00:00Z",
        },
      ],
    });

    render(<ReleaseCard envId="env" />);

    expect(screen.getByText("Release")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2026.11.1 · environment v3 · scenarios v4 · Train 54 · Test 14 · unlocked",
      ),
    ).toBeInTheDocument();
  });

  it("renders nothing before the server answers", () => {
    state.versions = undefined;

    const { container } = render(<ReleaseCard envId="env" />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("versionLine", () => {
  it("leaves out what an agent without a repository doesn't have", () => {
    expect(
      versionLine({
        agent: { label: "v2", source: { kind: "provider" } },
        environment: { label: "v1" },
        suite: { label: "v3" },
      }),
    ).toBe("agent v2 · environment v1 · scenarios v3");
  });
});
