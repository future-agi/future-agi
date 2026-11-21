import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The hooks are stubbed; tests set the drafts and each mutation's state.
const state = {
  drafts: undefined,
  save: {},
  discard: {},
  review: undefined,
  validate: {},
  saveValidated: {},
};
const validateMutate = vi.fn();
const saveValidatedMutate = vi.fn();
const saveValidatedReset = vi.fn();
const saveMutate = vi.fn();
const discardMutate = vi.fn();
const saveReset = vi.fn();
const discardReset = vi.fn();
// The review drawer has its own tests; the bar only opens it.
const reviewDrawer = vi.hoisted(() => vi.fn(() => null));
vi.mock("../ReviewChangesDrawer", () => ({ default: reviewDrawer }));
const queryClient = vi.hoisted(() => ({ invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => queryClient,
}));
const runningValidation = vi.hoisted(() => ({ data: undefined }));
vi.mock("src/api/harness/scenarios", () => ({
  useRunningScenarioValidation: () => runningValidation,
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
    useDiscardDraft: vi.fn(() => ({
      mutate: discardMutate,
      reset: discardReset,
      isPending: false,
      error: null,
      ...state.discard,
    })),
    useDraftReview: vi.fn(() => ({ data: state.review })),
    useValidateDrafts: vi.fn(() => ({
      mutate: validateMutate,
      isPending: false,
      error: null,
      ...state.validate,
    })),
    useSaveCandidates: vi.fn(() => ({
      mutate: saveValidatedMutate,
      reset: saveValidatedReset,
      isPending: false,
      error: null,
      ...state.saveValidated,
    })),
  };
});

const { default: DraftBar } = await import("../DraftBar");
const { DRAFTS_COPY } = await import("../../workspace.constants");
const { OUTDATED_NOTE } = await import("../useDraftsValidation");

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
  runningValidation.data = undefined;
  state.drafts = both();
  state.save = {};
  state.discard = {};
  state.review = undefined;
  state.validate = {};
  state.saveValidated = {};
  validateMutate.mockReset();
  saveValidatedMutate.mockReset();
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
    expect(saveMutate).toHaveBeenCalledWith(
      [
        { piece: "environment", parentId: "e2", revision: 3 },
        { piece: "suite", parentId: "s3", revision: 1 },
      ],
      { onSuccess: expect.any(Function) },
    );
  });

  it("keeps Save off while a validation of the scenarios runs, saying to wait, and frees it after", async () => {
    runningValidation.data = { job_id: "validation-1" };
    const { rerender } = render(<DraftBar env={ENV} />);
    const reason = DRAFTS_COPY.errors.validation_running;
    expect(screen.getByTestId("save-waits-on-validation")).toHaveTextContent(
      reason,
    );
    expect(saveButton()).toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(reason);

    runningValidation.data = { job_id: null };
    rerender(<DraftBar env={ENV} />);
    expect(saveButton()).toBeEnabled();
    expect(screen.queryByTestId("save-waits-on-validation")).toBeNull();
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
    ["chat_working", DRAFTS_COPY.errors.chat_working],
    ["rebuild_running", DRAFTS_COPY.errors.rebuild_running],
    ["environment_busy", DRAFTS_COPY.errors.environment_busy],
    ["draft_not_found", DRAFTS_COPY.errors.draft_not_found],
    ["idempotency_key_reused", DRAFTS_COPY.errors.idempotency_key_reused],
    ["scenario_index_mismatch", DRAFTS_COPY.errors.scenario_index_mismatch],
    [
      "change_depends_on_rejected",
      DRAFTS_COPY.errors.change_depends_on_rejected,
    ],
  ])("explains a save refused with %s", (code, text) => {
    state.save = { error: { statusCode: 409, error: code, message: "raw" } };
    render(<DraftBar env={ENV} />);
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });

  it.each(["save", "validate", "saveValidated"])(
    "says how the edits moved in the server's words when a %s is refused with draft_changed",
    (which) => {
      state[which] = {
        error: { statusCode: 409, error: "draft_changed", message: "These changes moved since this page read them; look at them again, then try again." },
      };
      if (which !== "save") state.review = { counts: { accepted: 1 } };
      render(<DraftBar env={ENV} />);
      expect(screen.getByRole("alert")).toHaveTextContent("These changes moved since this page read them; look at them again, then try again.");
      expect(screen.queryByText(/Someone changed/)).toBeNull();
    },
  );

  it("offers Validate when a save is refused for needing it", () => {
    state.save = {
      error: {
        statusCode: 409,
        error: "validation_required",
        message:
          "Validate the reviewed changes first, then save the validated versions",
      },
    };
    render(<DraftBar env={ENV} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      DRAFTS_COPY.errors.validation_required,
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.getByRole("button", { name: DRAFTS_COPY.validate }),
    ).toBeEnabled();
  });

  describe("validate before save", () => {
    const DIGEST = "sha256:review";
    const decided = () => {
      state.review = {
        review_digest: DIGEST,
        counts: { total: 3, accepted: 1, rejected: 1, undecided: 1 },
      };
    };
    const withValidation = (validation, candidates = null) => {
      state.drafts = { ...both(), validation, candidates };
    };

    it("asks to validate decided changes, sending the revisions and digest reviewed", async () => {
      validateMutate.mockClear();
      state.review = {
        review_digest: DIGEST,
        counts: { total: 3, accepted: 2, rejected: 1, undecided: 0 },
      };
      render(<DraftBar env={ENV} />);

      expect(
        screen.queryByRole("button", { name: DRAFTS_COPY.save }),
      ).toBeNull();
      await userEvent.click(
        screen.getByRole("button", { name: DRAFTS_COPY.validate }),
      );

      expect(validateMutate.mock.calls[0][0]).toEqual({
        expectedRevisions: { environment: 3, suite: 1 },
        expectedReviewDigest: DIGEST,
      });
    });

    it("opens the review to finish deciding, instead of a Validate the server refuses", async () => {
      validateMutate.mockClear();
      decided();
      render(<DraftBar env={ENV} />);

      await userEvent.click(
        screen.getByRole("button", { name: DRAFTS_COPY.validate }),
      );

      expect(validateMutate).not.toHaveBeenCalled();
      expect(reviewDrawer.mock.lastCall[0].open).toBe(true);
    });

    it("reads the drafts and the review again when a Validate finds them behind", async () => {
      validateMutate.mockClear();
      state.review = {
        review_digest: DIGEST,
        counts: { total: 3, accepted: 2, rejected: 1, undecided: 0 },
      };
      render(<DraftBar env={ENV} />);
      await userEvent.click(
        screen.getByRole("button", { name: DRAFTS_COPY.validate }),
      );
      const spy = queryClient.invalidateQueries;
      spy.mockClear();

      validateMutate.mock.calls[0][1].onError({ error: "draft_changed" });

      const keys = spy.mock.calls.map(([arg]) => JSON.stringify(arg.queryKey));
      expect(keys).toContain(JSON.stringify(["harness-draft-review", "env-1"]));
      expect(keys.some((key) => key.includes('"drafts"'))).toBe(true);
    });

    it("keeps Save off while validating", () => {
      decided();
      withValidation({ state: "validating" });
      render(<DraftBar env={ENV} />);

      expect(
        screen.getByRole("button", { name: DRAFTS_COPY.validating }),
      ).toBeDisabled();
      expect(
        screen.queryByRole("button", { name: DRAFTS_COPY.save }),
      ).toBeNull();
    });

    it("saves the validated candidates once ready", async () => {
      decided();
      withValidation(
        { state: "ready" },
        { environment_version_id: "e9", suite_version_id: "s9" },
      );
      render(<DraftBar env={ENV} />);

      // The new version needs a name before it can be saved.
      expect(saveButton()).toBeDisabled();
      await userEvent.type(
        screen.getByRole("textbox", { name: DRAFTS_COPY.versionName }),
        " Finance controller ",
      );
      await userEvent.click(saveButton());

      expect(saveValidatedMutate.mock.calls.at(-1)[0]).toEqual({
        candidates: { environmentVersionId: "e9", suiteVersionId: "s9" },
        expectedReviewDigest: DIGEST,
        name: "Finance controller",
      });
      expect(saveMutate).not.toHaveBeenCalled();
    });

    it("keeps a candidate save refused as moved, rereading the drafts so the bar asks to Validate again", async () => {
      decided();
      withValidation(
        { state: "ready" },
        { environment_version_id: "e9", suite_version_id: "s9" },
      );
      render(<DraftBar env={ENV} />);
      await userEvent.type(
        screen.getByRole("textbox", { name: DRAFTS_COPY.versionName }),
        "Finance controller",
      );
      await userEvent.click(saveButton());
      queryClient.invalidateQueries.mockClear();
      saveValidatedReset.mockClear();

      saveValidatedMutate.mock.calls.at(-1)[1].onError({
        error: "draft_changed",
      });

      const keys = queryClient.invalidateQueries.mock.calls.map(([arg]) =>
        JSON.stringify(arg.queryKey),
      );
      expect(keys).toContain(JSON.stringify(["harness-draft-review", "env-1"]));
      expect(keys.some((key) => key.includes('"drafts"'))).toBe(true);
      // The refusal stays, in the server's words.
      expect(saveValidatedReset).not.toHaveBeenCalled();
    });

    it("asks to Validate again once the validated changes moved, in its own words", () => {
      decided();
      withValidation({ state: "outdated" });
      render(<DraftBar env={ENV} />);

      expect(screen.getByText(OUTDATED_NOTE)).toBeInTheDocument();
      expect(screen.queryByText(DRAFTS_COPY.validation.superseded)).toBeNull();
      expect(
        screen.getByRole("button", { name: DRAFTS_COPY.validate }),
      ).toBeEnabled();
    });

    it("says a validated change needs a name before Save", () => {
      decided();
      withValidation(
        { state: "ready" },
        { environment_version_id: "e9", suite_version_id: "s9" },
      );
      render(<DraftBar env={ENV} />);

      expect(saveButton()).toBeDisabled();
      expect(screen.getByRole("status")).toHaveTextContent(
        DRAFTS_COPY.validation.ready,
      );
    });

    it("names the scenarios a failed validation needs changed and keeps Save off", () => {
      decided();
      withValidation({
        state: "failed",
        affected_scenarios: ["refund-1", "refund-2"],
      });
      render(<DraftBar env={ENV} />);

      expect(
        screen.getByText(DRAFTS_COPY.validation.failed(2)),
      ).toBeInTheDocument();
      expect(screen.getByText("refund-1")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: DRAFTS_COPY.save }),
      ).toBeNull();
    });

    it.each([
      ["retry", DRAFTS_COPY.validation.retry],
      ["superseded", DRAFTS_COPY.validation.superseded],
    ])("offers Validate again after %s", (state_, copy) => {
      decided();
      withValidation({ state: state_ });
      render(<DraftBar env={ENV} />);

      expect(screen.getByText(copy)).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: DRAFTS_COPY.validate }),
      ).toBeEnabled();
    });

    it("saves without a check when the check is unavailable on our side, saying so", async () => {
      decided();
      withValidation(
        { state: "check_unavailable" },
        { environment_version_id: "e9", suite_version_id: "s9" },
      );
      render(<DraftBar env={ENV} />);

      expect(
        screen.getByText(DRAFTS_COPY.validation.check_unavailable),
      ).toBeInTheDocument();
      await userEvent.type(
        screen.getByRole("textbox", { name: DRAFTS_COPY.versionName }),
        "v10",
      );
      expect(saveButton()).toBeEnabled();
    });
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

  it("opens the review of the changes, not the Versions tab", async () => {
    const onViewChanges = vi.fn();
    state.drafts = { environment: null, suite: SUITE_DRAFT, set_aside: [] };
    render(<DraftBar env={ENV} onViewChanges={onViewChanges} />);
    expect(reviewDrawer.mock.lastCall[0].open).toBe(false);
    await userEvent.click(
      screen.getByRole("button", { name: DRAFTS_COPY.viewChanges }),
    );
    expect(reviewDrawer.mock.lastCall[0].open).toBe(true);
    expect(onViewChanges).not.toHaveBeenCalled();
  });
});
