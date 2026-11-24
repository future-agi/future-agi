import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

// The hooks are stubbed; tests set the drafts and each mutation's state.
const state = {
  drafts: undefined,
  save: {},
  discard: {},
};
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
      mutate: vi.fn(),
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
    useDraftReview: vi.fn(() => ({ data: undefined })),
    useValidateDrafts: vi.fn(() => ({
      mutate: vi.fn(),
      isPending: false,
      error: null,
    })),
    useSaveCandidates: vi.fn(() => ({
      mutate: vi.fn(),
      reset: vi.fn(),
      isPending: false,
      error: null,
    })),
  };
});

vi.mock(
  "src/api/simulate-environments/scenariosHooks",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRunningScenarioValidation: () => ({ data: state.running }),
  }),
);

const { default: DraftBar } = await import("../DraftBar");
const { DRAFTS_COPY, REVIEW_COPY } = await import("../../workspace.constants");

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

const reviewButton = () =>
  screen.getByRole("button", { name: REVIEW_COPY.reviewAndSave });
const discardButton = () =>
  screen.getByRole("button", { name: DRAFTS_COPY.discard });

beforeEach(() => {
  state.drafts = both();
  state.save = {};
  state.discard = {};
  discardMutate.mockReset();
  saveReset.mockReset();
  discardReset.mockReset();
});

describe("DraftBar", () => {
  it("shows nothing with versioning off", () => {
    render(
      <DraftBar
        env={{ id: "env-1", versioning: { enabled: false } }}
        onReview={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("draft-bar")).toBeNull();
  });

  it("shows nothing without open drafts", () => {
    state.drafts = { environment: null, suite: null, set_aside: [] };
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(screen.queryByTestId("draft-bar")).toBeNull();
  });

  it("states what each piece changes and who edited last", () => {
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    const bar = screen.getByRole("region", { name: DRAFTS_COPY.title });
    expect(bar).toHaveTextContent("environment · 3 changes");
    expect(bar).toHaveTextContent("scenarios · 2 added, 1 removed, 4 changed");
    expect(bar).toHaveTextContent("last edited by Asha, 5 minutes ago");
  });

  it("keeps the review off with the reason in view when the drafts are out of date", async () => {
    state.drafts = both({
      suite: { stale: true, parent_label: "v3", default_label: "v4" },
    });
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    const reason = DRAFTS_COPY.stale("scenarios", "v3", "v4");
    expect(screen.getByText(reason)).toBeVisible();
    expect(reviewButton()).toBeDisabled();
    expect(reviewButton()).toHaveAccessibleDescription(reason);
    // Discarding is the way out.
    expect(discardButton()).toBeEnabled();
  });

  it("keeps both buttons off while chat is working, saying why", () => {
    render(<DraftBar env={ENV} busy onReview={vi.fn()} />);
    expect(screen.getByText(DRAFTS_COPY.busy)).toBeVisible();
    expect(reviewButton()).toBeDisabled();
    expect(discardButton()).toBeDisabled();
    expect(reviewButton()).toHaveAccessibleDescription(DRAFTS_COPY.busy);
    expect(discardButton()).toHaveAccessibleDescription(DRAFTS_COPY.busy);
  });

  it("keeps both buttons off while a rebuild holds the drafts, saying why", () => {
    render(<DraftBar env={ENV} rebuilding onReview={vi.fn()} />);
    const reason = DRAFTS_COPY.errors.rebuild_running;
    expect(screen.getByText(reason)).toBeVisible();
    expect(reviewButton()).toBeDisabled();
    expect(discardButton()).toBeDisabled();
    expect(reviewButton()).toHaveAccessibleDescription(reason);
    expect(discardButton()).toHaveAccessibleDescription(reason);
  });

  it("shows the discard in progress", () => {
    state.discard = { isPending: true };
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: DRAFTS_COPY.discarding }),
    ).toBeDisabled();
    expect(reviewButton()).toBeDisabled();
  });

  it.each([
    ["draft_changed", DRAFTS_COPY.errors.draft_changed],
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
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });

  it("asks for Validate when a save is refused for needing it", () => {
    state.save = {
      error: { statusCode: 409, error: "validation_required", message: "x" },
    };
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      DRAFTS_COPY.errors.validation_required,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("opens the review, also while the changes are checked and for a viewer", async () => {
    const { default: useSimulationAccess } = await import(
      "src/sections/simulate/environments/hooks/useSimulationAccess"
    );
    useSimulationAccess.mockReturnValueOnce({ canEdit: false, canRun: false });
    state.drafts = { ...both(), validation: { state: "validating" } };
    const onReview = vi.fn();
    render(<DraftBar env={ENV} onReview={onReview} />);
    expect(discardButton()).toBeDisabled();
    await userEvent.click(reviewButton());
    expect(onReview).toHaveBeenCalled();
  });

  it("names the scenarios a failed validation needs changed", () => {
    state.drafts = {
      ...both(),
      validation: { state: "failed", affected_scenarios: ["refund-1"] },
    };
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(
      screen.getByText(DRAFTS_COPY.validation.failed(1)),
    ).toBeInTheDocument();
    expect(screen.getByText("refund-1")).toBeInTheDocument();
  });

  it("explains a stale refusal with the piece it names", () => {
    state.save = {
      error: { statusCode: 409, error: "draft_stale", piece: "environment" },
    };
    state.drafts = both({ environment: { default_label: "v3" } });
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
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
      render(<DraftBar env={ENV} onReview={vi.fn()} />);
      expect(screen.getByRole("alert")).toHaveTextContent(
        "These changes can't be made live yet: no world",
      );
    },
  );

  it("falls back to its own words when a failure carries none", () => {
    state.save = { error: { message: "Network Error" } };
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(DRAFTS_COPY.saveError);
  });

  it("explains a refused discard", () => {
    state.discard = { error: { statusCode: 409, error: "chat_working" } };
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
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
    const { rerender } = render(<DraftBar env={ENV} onReview={vi.fn()} />);
    saveReset.mockClear();
    discardReset.mockClear();
    rerender(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(saveReset).not.toHaveBeenCalled();

    state.drafts = both({ suite: { revision: 2 } });
    rerender(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(saveReset).toHaveBeenCalledTimes(1);
    expect(discardReset).toHaveBeenCalledTimes(1);
  });

  it("keeps a draft_changed refusal over the drafts it refetched", () => {
    state.save = {
      isError: true,
      error: { statusCode: 409, error: "draft_changed" },
    };
    const { rerender } = render(<DraftBar env={ENV} onReview={vi.fn()} />);
    state.drafts = both({ suite: { revision: 2 } });
    rerender(<DraftBar env={ENV} onReview={vi.fn()} />);
    expect(saveReset).not.toHaveBeenCalled();
  });

  it("asks before discarding, then discards every open piece", async () => {
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
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
    render(<DraftBar env={ENV} onReview={vi.fn()} />);
    await userEvent.click(discardButton());
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(discardMutate).not.toHaveBeenCalled();
  });
});
