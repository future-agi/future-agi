import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import type {
  HarnessLockPreviewApi,
  HarnessLockedVersionApi,
} from "src/generated/api-contracts/api.schemas";
import type { LockedVersion, VersionPair } from "../LockedVersionCard";

// A version row as the read sends it, with only the fields the card reads.
interface PieceRow {
  id: string;
  label: string;
  status: string;
  is_default: boolean;
  source?: { repository: string; ref: string; commit_sha: string };
}

// The versions read's body, with only the fields the card reads.
interface VersionsBody {
  agent: PieceRow[];
  environment: PieceRow[];
  suite: PieceRow[];
  locked_versions: HarnessLockedVersionApi[];
  lock: HarnessLockPreviewApi;
}

// What the server answered for the versions read, and what each action got.
const state: {
  versions: VersionsBody | undefined;
  canUnlockVersion: boolean;
  canEdit: boolean;
} = { versions: undefined, canUnlockVersion: true, canEdit: true };
const calls: {
  lock: VersionPair[];
  unlock: { lockedVersionId: LockedVersion["id"] }[];
} = {
  lock: [],
  unlock: [],
};

vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual<
    typeof import("src/api/simulate-environments/versions")
  >("src/api/simulate-environments/versions");
  return {
    ...actual,
    useEnvironmentVersions: vi.fn(() => ({
      data: state.versions ? actual.toVersions(state.versions) : undefined,
    })),
    useLockVersion: vi.fn(() => ({
      isPending: false,
      mutate: (variables: VersionPair) => calls.lock.push(variables),
    })),
    useUnlockVersion: vi.fn(() => ({
      isPending: false,
      mutate: (variables: { lockedVersionId: string }) =>
        calls.unlock.push(variables),
    })),
  };
});
vi.mock("../../../hooks/useSimulationAccess", () => ({
  default: () => ({
    canEdit: state.canEdit,
    canRun: true,
    canUnlockVersion: state.canUnlockVersion,
  }),
}));

const { default: LockedVersionCard } = await import("../LockedVersionCard");
const { versionLine } = await import("../overview.constants");

const piece = (id: string, label: string): PieceRow => ({
  id,
  label,
  status: "active",
  is_default: true,
});
const LOCKED_VERSION: HarnessLockedVersionApi = {
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
const answer = ({
  lockedVersions = [],
  lock = {},
}: {
  lockedVersions?: HarnessLockedVersionApi[];
  lock?: Partial<HarnessLockPreviewApi>;
} = {}): VersionsBody => ({
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
  locked_versions: lockedVersions,
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
  state.canUnlockVersion = true;
  state.canEdit = true;
  calls.lock.length = 0;
  calls.unlock.length = 0;
});

describe("LockedVersionCard", () => {
  it("states the version line", () => {
    render(<LockedVersionCard envId="env" />);

    expect(
      screen.getByText(
        "agent v4 · acme/support-bot@main · a1b2c3d · environment v3 · scenarios v5",
      ),
    ).toBeInTheDocument();
  });

  it("offers the lock with the splits it would get", async () => {
    render(<LockedVersionCard envId="env" />);

    expect(screen.getByText("Lock a version")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Not proved")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Train 54 · Test 14 scenarios once locked. Lock to freeze scenarios, splits and the scorer.",
      ),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Lock this version" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Lock this version?")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Its scenarios, train and test splits and scorer freeze, so every score on it stays comparable. Each scenario keeps its split in every later locked version. Edits make a new, unlocked version.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Lock version" }),
    );

    expect(calls.lock).toEqual([
      { environmentVersionId: "env-3", suiteVersionId: "suite-5" },
    ]);
  });

  it("locks the pair that opened the dialog, even if the default moved since", async () => {
    const { rerender } = render(<LockedVersionCard envId="env" />);
    await userEvent.click(
      screen.getByRole("button", { name: "Lock this version" }),
    );

    // The versions refresh behind the open dialog with a newer default pair.
    state.versions = answer({
      lock: { environment_version_id: "env-4", suite_version_id: "suite-6" },
    });
    rerender(<LockedVersionCard envId="env" />);
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Lock version",
      }),
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
          code: "lock_too_few_test",
          message: "Add scenarios to lock: a test split needs at least 4.",
        },
        train: 7,
        test: 3,
      },
    });

    const onGo = vi.fn();
    render(<LockedVersionCard envId="env" onGo={onGo} />);

    expect(screen.getByText("Can't lock yet")).toBeInTheDocument();
    expect(
      screen.getByText("Add scenarios to lock: a test split needs at least 4."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Lock this version" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Add scenarios" }),
    );
    expect(onGo).toHaveBeenCalledWith("scenarios");
  });

  it("offers no action when the build, not the suite, is the reason", () => {
    state.versions = answer({
      lock: {
        can_lock: false,
        refusal: {
          code: "lock_not_ready",
          message:
            "This environment is building or its last build failed. Lock it once a build finishes.",
        },
      },
    });

    render(<LockedVersionCard envId="env" onGo={vi.fn()} />);

    expect(
      screen.queryByRole("button", { name: "Add scenarios" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the lock from someone who can't edit, and says why", async () => {
    state.canEdit = false;

    render(<LockedVersionCard envId="env" />);

    expect(
      screen.getByRole("button", { name: "Lock this version" }),
    ).toBeDisabled();
    await userEvent.tab();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "You don't have permission to lock versions.",
    );
  });

  it("shows a locked version with its splits, and an admin can unlock it", async () => {
    state.versions = answer({
      lockedVersions: [LOCKED_VERSION],
      lock: { can_lock: false, proved: true },
    });

    render(<LockedVersionCard envId="env" />);

    expect(screen.getByText("Locked version 2026.11.1")).toBeInTheDocument();
    // The title already says Locked: a locked card shows no status badge.
    expect(screen.queryByText("Locked")).not.toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(
      screen.getByText(/Train 54 · Test 14 scenarios · scorer environment v3$/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Lock this version" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Unlock version 2026.11.1?"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Its scores stay, but they're no longer a locked comparison. The train/test split of each scenario never changes.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Unlock version" }),
    );

    expect(calls.unlock).toEqual([{ lockedVersionId: "rel-1" }]);
  });

  it("hides Unlock from a member", () => {
    state.canUnlockVersion = false;
    state.versions = answer({
      lockedVersions: [LOCKED_VERSION],
      lock: { can_lock: false },
    });

    render(<LockedVersionCard envId="env" />);

    expect(
      screen.queryByRole("button", { name: "Unlock" }),
    ).not.toBeInTheDocument();
  });

  it("lets an admin unlock an earlier locked version the default moved past", async () => {
    state.versions = answer({
      lockedVersions: [
        { ...LOCKED_VERSION, suite_version_id: "suite-4", suite_label: "v4" },
      ],
    });

    render(<LockedVersionCard envId="env" />);

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2026.11.1 · environment v3 · scenarios v4 · Train 54 · Test 14 · locked",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Unlock version 2026.11.1?"),
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Unlock version" }),
    );

    expect(calls.unlock).toEqual([{ lockedVersionId: "rel-1" }]);
  });

  it("lists earlier locked versions, unlocked ones included", () => {
    state.versions = answer({
      lockedVersions: [
        {
          ...LOCKED_VERSION,
          id: "rel-0",
          label: "2026.11.1",
          suite_version_id: "suite-4",
          suite_label: "v4",
          unlocked_at: "2026-11-26T08:00:00Z",
        },
      ],
    });

    render(<LockedVersionCard envId="env" />);

    expect(screen.getByText("Lock a version")).toBeInTheDocument();
    expect(
      screen.getByText(
        "2026.11.1 · environment v3 · scenarios v4 · Train 54 · Test 14 · unlocked",
      ),
    ).toBeInTheDocument();
  });

  it("renders nothing before the server answers", () => {
    state.versions = undefined;

    const { container } = render(<LockedVersionCard envId="env" />);

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
