import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The hooks are stubbed; tests set the drafts and each mutation's state.
const state = { drafts: undefined, save: {}, review: null };
const saveMutate = vi.fn();
const saveReset = vi.fn();
vi.mock("src/api/simulate-environments/draftReview", () => ({
  useDraftReview: vi.fn(() => ({ data: state.review })),
}));
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
    useEnvironmentVersions: vi.fn(() => ({
      data: {
        defaults: { environment: { label: "v2" }, suite: { label: "v3" } },
      },
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

const KEPT = {
  id: "rule:a",
  piece: "environment",
  kind: "rule",
  decision: null,
  revalidate: ["s1", "s2"],
};
const saveButton = () => screen.getByRole("button", { name: /^Save release/ });

beforeEach(() => {
  state.drafts = both();
  state.save = {};
  state.review = { changes: [KEPT], failed: [] };
  saveMutate.mockReset();
  saveReset.mockReset();
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

  it("states the edits and what saving makes, and opens the review", async () => {
    const onReview = vi.fn();
    render(<DraftBar env={ENV} onReview={onReview} />);
    await userEvent.click(
      screen.getByRole("button", {
        name: /1 unsaved edit.*environment v3.*2 scenarios rechecked/,
      }),
    );
    expect(onReview).toHaveBeenCalled();
    expect(saveButton()).toHaveTextContent("Save release v3");
  });

  it("offers no Save when only failed changes are left", () => {
    state.review = {
      changes: [{ ...KEPT, decision: "rejected" }],
      failed: [{ id: "f" }],
    };
    render(<DraftBar env={ENV} />);
    expect(
      screen.getByRole("button", { name: /1 change couldn't apply/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Save release/ })).toBeNull();
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
  });

  it("keeps Save off while chat is working, saying why", () => {
    render(<DraftBar env={ENV} busy />);
    expect(screen.getByText(DRAFTS_COPY.busy)).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(DRAFTS_COPY.busy);
  });

  it("keeps Save off while a rebuild holds the drafts, saying why", () => {
    render(<DraftBar env={ENV} rebuilding />);
    const reason = DRAFTS_COPY.errors.rebuild_running;
    expect(screen.getByText(reason)).toBeVisible();
    expect(saveButton()).toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(reason);
  });

  it("shows the save in progress and blocks a second action", () => {
    state.save = { isPending: true };
    render(<DraftBar env={ENV} />);
    expect(
      screen.getByRole("button", { name: DRAFTS_COPY.saving }),
    ).toBeDisabled();
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

  it("clears a refusal once the drafts it was about change", () => {
    state.save = {
      isError: true,
      error: { statusCode: 409, error: "chat_working" },
    };
    const { rerender } = render(<DraftBar env={ENV} />);
    saveReset.mockClear();
    rerender(<DraftBar env={ENV} />);
    expect(saveReset).not.toHaveBeenCalled();

    state.drafts = both({ suite: { revision: 2 } });
    rerender(<DraftBar env={ENV} />);
    expect(saveReset).toHaveBeenCalledTimes(1);
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
});
