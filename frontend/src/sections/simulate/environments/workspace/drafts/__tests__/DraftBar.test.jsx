import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The hooks are stubbed; tests set the drafts and each mutation's state.
const state = { drafts: undefined, save: {}, discard: {} };
const saveMutate = vi.fn();
const discardMutate = vi.fn();
const saveReset = vi.fn();
const discardReset = vi.fn();
vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  return {
    ...actual,
    useDrafts: vi.fn((envId, { enabled } = {}) => ({
      data: enabled && state.drafts ? actual.toDrafts(state.drafts) : undefined,
    })),
    useSaveDrafts: vi.fn(() => ({
      mutate: saveMutate,
      reset: saveReset,
      isPending: false,
      error: null,
      ...state.save,
    })),
    useDiscardDraft: vi.fn(() => ({
      mutate: discardMutate,
      reset: discardReset,
      isPending: false,
      error: null,
      ...state.discard,
    })),
  };
});

const { default: DraftBar } = await import("../DraftBar");
const { DRAFTS_COPY } = await import("../../workspace.constants");

const ENV = { id: "env-1", versioning: { enabled: true } };
const ENVIRONMENT_DRAFT = {
  id: "ed",
  status: "draft",
  parent_id: "e2",
  stale: false,
  revision: 3,
  last_edited_by: { id: "u1", name: "Asha" },
  updated_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  parent_label: "v2",
  default_label: "v2",
  changes: { tools: 1, rules: 2, sub_goals: 0, world_files: 0 },
};
const SUITE_DRAFT = {
  id: "sd",
  status: "draft",
  parent_id: "s3",
  stale: false,
  revision: 1,
  last_edited_by: { id: "u2", name: "Ravi" },
  updated_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
  parent_label: "v3",
  default_label: "v3",
  changes: { added: 2, removed: 1, changed: 4 },
};
const both = (overrides = {}) => ({
  environment: { ...ENVIRONMENT_DRAFT, ...overrides.environment },
  suite: { ...SUITE_DRAFT, ...overrides.suite },
  set_aside: [],
});

const saveButton = () => screen.getByRole("button", { name: DRAFTS_COPY.save });
const discardButton = () =>
  screen.getByRole("button", { name: DRAFTS_COPY.discard });

beforeEach(() => {
  state.drafts = both();
  state.save = {};
  state.discard = {};
  saveMutate.mockReset();
  discardMutate.mockReset();
  saveReset.mockReset();
  discardReset.mockReset();
});

describe("DraftBar", () => {
  it("shows nothing with versioning off", () => {
    render(<DraftBar env={{ id: "env-1", versioning: { enabled: false } }} />);
    expect(screen.queryByTestId("draft-bar")).toBeNull();
  });

  it("shows nothing without open drafts", () => {
    state.drafts = { environment: null, suite: null, set_aside: [] };
    render(<DraftBar env={ENV} />);
    expect(screen.queryByTestId("draft-bar")).toBeNull();
  });

  it("states what each piece changes and who edited last", () => {
    render(<DraftBar env={ENV} />);
    const bar = screen.getByRole("region", { name: DRAFTS_COPY.title });
    expect(bar).toHaveTextContent("environment · 3 changes");
    expect(bar).toHaveTextContent("scenarios · 2 added, 1 removed, 4 changed");
    expect(bar).toHaveTextContent("last edited by Asha, 5 minutes ago");
  });

  it("saves every open piece with the parent and revision it shows", async () => {
    render(<DraftBar env={ENV} />);
    await userEvent.click(saveButton());
    expect(saveMutate).toHaveBeenCalledWith([
      { piece: "environment", parentId: "e2", revision: 3 },
      { piece: "suite", parentId: "s3", revision: 1 },
    ]);
  });

  it("keeps Save off with the reason in view when the drafts are out of date", async () => {
    state.drafts = both({
      suite: { stale: true, parent_label: "v3", default_label: "v4" },
    });
    render(<DraftBar env={ENV} />);
    const reason = DRAFTS_COPY.stale("scenarios", "v3", "v4");
    expect(screen.getByText(reason)).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(reason);
    // Discarding is the way out.
    expect(discardButton()).toBeEnabled();
  });

  it("keeps both buttons off while chat is working, saying why", () => {
    render(<DraftBar env={ENV} busy />);
    expect(screen.getByText(DRAFTS_COPY.busy)).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(discardButton()).toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(DRAFTS_COPY.busy);
    expect(discardButton()).toHaveAccessibleDescription(DRAFTS_COPY.busy);
  });

  it("keeps both buttons off while a rebuild holds the drafts, saying why", () => {
    render(<DraftBar env={ENV} rebuilding />);
    const reason = DRAFTS_COPY.errors.rebuild_running;
    expect(screen.getByText(reason)).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(discardButton()).toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(reason);
    expect(discardButton()).toHaveAccessibleDescription(reason);
    expect(screen.getByRole("button", { name: "View changes" })).toBeEnabled();
  });

  it("shows the save in progress and blocks a second action", () => {
    state.save = { isPending: true };
    render(<DraftBar env={ENV} />);
    expect(
      screen.getByRole("button", { name: DRAFTS_COPY.saving }),
    ).toBeDisabled();
    expect(discardButton()).toBeDisabled();
  });

  it("shows the discard in progress", () => {
    state.discard = { isPending: true };
    render(<DraftBar env={ENV} />);
    expect(
      screen.getByRole("button", { name: DRAFTS_COPY.discarding }),
    ).toBeDisabled();
    expect(saveButton()).toBeDisabled();
  });

  it.each([
    ["draft_changed", DRAFTS_COPY.errors.draft_changed],
    ["chat_working", DRAFTS_COPY.errors.chat_working],
    ["rebuild_running", DRAFTS_COPY.errors.rebuild_running],
    ["environment_busy", DRAFTS_COPY.errors.environment_busy],
    ["draft_not_found", DRAFTS_COPY.errors.draft_not_found],
    ["idempotency_key_reused", DRAFTS_COPY.errors.idempotency_key_reused],
  ])("explains a save refused with %s", (code, text) => {
    state.save = { error: { statusCode: 409, error: code, message: "raw" } };
    render(<DraftBar env={ENV} />);
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });

  it("explains a stale refusal with the piece it names", () => {
    state.save = {
      error: { statusCode: 409, error: "draft_stale", piece: "environment" },
    };
    state.drafts = both({ environment: { default_label: "v3" } });
    render(<DraftBar env={ENV} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      DRAFTS_COPY.stale("environment", "v2", "v3"),
    );
  });

  it.each(["draft_not_runnable", "scenario_key_retired", "scenario_key_taken"])(
    "quotes the server for %s",
    (code) => {
      state.save = {
        error: {
          statusCode: 422,
          error: code,
          message: "These changes can't be made live yet: no world",
        },
      };
      render(<DraftBar env={ENV} />);
      expect(screen.getByRole("alert")).toHaveTextContent(
        "These changes can't be made live yet: no world",
      );
    },
  );

  it("falls back to its own words when a failure carries none", () => {
    state.save = { error: { message: "Network Error" } };
    render(<DraftBar env={ENV} />);
    expect(screen.getByRole("alert")).toHaveTextContent(DRAFTS_COPY.saveError);
  });

  it("explains a refused discard", () => {
    state.discard = { error: { statusCode: 409, error: "chat_working" } };
    render(<DraftBar env={ENV} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      DRAFTS_COPY.errors.chat_working,
    );
  });

  it("clears a refusal once the drafts it was about change", () => {
    state.save = {
      isError: true,
      error: { statusCode: 409, error: "chat_working" },
    };
    state.discard = {
      isError: true,
      error: { statusCode: 409, error: "chat_working" },
    };
    const { rerender } = render(<DraftBar env={ENV} />);
    saveReset.mockClear();
    discardReset.mockClear();
    rerender(<DraftBar env={ENV} />);
    expect(saveReset).not.toHaveBeenCalled();

    state.drafts = both({ suite: { revision: 2 } });
    rerender(<DraftBar env={ENV} />);
    expect(saveReset).toHaveBeenCalledTimes(1);
    expect(discardReset).toHaveBeenCalledTimes(1);
  });

  it("keeps a draft_changed refusal over the drafts it refetched", () => {
    state.save = {
      isError: true,
      error: { statusCode: 409, error: "draft_changed" },
    };
    const { rerender } = render(<DraftBar env={ENV} />);
    state.drafts = both({ suite: { revision: 2 } });
    rerender(<DraftBar env={ENV} />);
    expect(saveReset).not.toHaveBeenCalled();
  });

  it("asks before discarding, then discards every open piece", async () => {
    render(<DraftBar env={ENV} />);
    await userEvent.click(discardButton());
    expect(discardMutate).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(DRAFTS_COPY.discardTitle);

    await userEvent.click(
      within(dialog).getByRole("button", { name: DRAFTS_COPY.discard }),
    );
    expect(discardMutate).toHaveBeenCalledTimes(1);
    expect(discardMutate).toHaveBeenCalledWith(["environment", "suite"]);
  });

  it("discards nothing when the question is closed", async () => {
    render(<DraftBar env={ENV} />);
    await userEvent.click(discardButton());
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(discardMutate).not.toHaveBeenCalled();
  });

  it("opens the first open draft's changes", async () => {
    const onViewChanges = vi.fn();
    state.drafts = { environment: null, suite: SUITE_DRAFT, set_aside: [] };
    render(<DraftBar env={ENV} onViewChanges={onViewChanges} />);
    await userEvent.click(
      screen.getByRole("button", { name: DRAFTS_COPY.viewChanges }),
    );
    expect(onViewChanges).toHaveBeenCalledWith("suite", "sd");
  });
});
