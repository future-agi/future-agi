import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  CODE_EVAL,
  CUSTOM_EVAL,
  LIBRARY_TEXT_EVAL,
  LIBRARY_VOICE_EVAL,
  NO_MISSELLING,
  NO_MISSELLING_UNALIGNED,
  selectedEntry,
} from "./fixtures/evalEntries";

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  getAvailableEvaluations: vi.fn(),
  addEvaluation: vi.fn(),
  addRunEvaluation: vi.fn(),
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  getHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(),
}));

const { getAvailableEvaluations, addEvaluation, addRunEvaluation, getHarnessEnvironment } =
  await import("src/api/simulate-environments/harnessEnvironments");
const { default: AddEvaluationDrawer } = await import("../AddEvaluationDrawer");

const render = (ui) =>
  rtlRender(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      {ui}
    </QueryClientProvider>,
  );

const ENV = { id: "env-1" };

beforeEach(() => {
  getAvailableEvaluations.mockReset();
  getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING] });
  getHarnessEnvironment.mockReset();
  getHarnessEnvironment.mockResolvedValue({ evaluations: { selected: [] } });
  addEvaluation.mockReset();
  addEvaluation.mockResolvedValue({ evaluations: { selected: [] } });
  addRunEvaluation.mockReset();
});

describe("AddEvaluationDrawer — the row", () => {
  it("shows the API's own labels for what fills each key, and never the raw source", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    // The arrows are behind the expander.
    expect(screen.queryByText("Call recording")).toBeNull();

    fireEvent.click(screen.getByText("no_misselling"));

    expect(screen.getByText("{{agent_prompt}}")).toBeInTheDocument();
    expect(screen.getByText("Agent instructions")).toBeInTheDocument();
    expect(screen.getByText("{{conversation}}")).toBeInTheDocument();
    expect(screen.getByText("Call recording")).toBeInTheDocument();
    // The description shows in the expanded panel too (EvalDetail).
    expect(screen.getByText(NO_MISSELLING.description)).toBeInTheDocument();
    // `label` is the only text shown for a source.
    expect(screen.queryByText("voice_recording")).toBeNull();
    expect(screen.queryByText("agent_prompt")).toBeNull();
    // Read-only: nothing to edit.
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("pairs a key with its label by key, never by the order of required_keys", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING_UNALIGNED] });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByText("no_misselling"));

    const rows = screen.getAllByText(/^\{\{.+\}\}$/).map((el) => el.textContent);
    // The order on screen is `inputs`' (sorted by key), not required_keys'.
    expect(rows).toEqual(["{{agent_prompt}}", "{{conversation}}"]);
  });

  // Every entry in ONE `available` response carries the same `agent_type`,
  // so all three here are `"chat"` — mixing a voice entry in would build a
  // response the server cannot produce.
  it("shows Library/Custom and the cost line, built from the two fields", async () => {
    getAvailableEvaluations.mockResolvedValue({
      evaluations: [LIBRARY_TEXT_EVAL, CUSTOM_EVAL, CODE_EVAL],
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    await screen.findByText("no_pii_leak");
    expect(screen.getAllByText("Library")).toHaveLength(2);
    expect(screen.getAllByText("Custom")).toHaveLength(1);
    expect(screen.getAllByText("0.5 credits per run + judge tokens")).toHaveLength(2);
    expect(screen.getAllByText("0.5 credits per run")).toHaveLength(1);
  });

  it("gives the row's expander an accessible name and announces its state", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    // The name carries the eval so two rows (offer and bound) never
    // announce as identical "Expand" buttons.
    const expander = screen.getByRole("button", { name: "Expand no_misselling" });
    expect(expander).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(expander);

    const collapse = screen.getByRole("button", { name: "Collapse no_misselling" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Call recording")).toBeInTheDocument();
  });
});

describe("AddEvaluationDrawer — adding to the environment", () => {
  it("posts the name and nothing else", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    await waitFor(() =>
      expect(addEvaluation).toHaveBeenCalledWith("env-1", "no_misselling"),
    );
    expect(addEvaluation).toHaveBeenCalledTimes(1);
  });

  // The only thing standing between a double click and a second POST is the
  // offer button's `addMutation.isPending` term. Removing it (leaving
  // `disabled={added || atCap}`) leaves every other drawer test green, so
  // this is the one that pins it — mirroring the bound group's own
  // in-flight case below.
  it("issues one POST when Add is clicked twice while the first is still in flight", async () => {
    addEvaluation.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));

    // Wait for the pending state to land (react-query notifies subscribers
    // async) so the second click genuinely races an in-flight mutation
    // rather than a stale pre-click render.
    const pending = await screen.findByRole("button", { name: "…" });
    fireEvent.click(pending);
    fireEvent.click(pending);

    // One POST, not three — the assertion the guard exists for.
    await waitFor(() => expect(addEvaluation).toHaveBeenCalledTimes(1));
    expect(addEvaluation).toHaveBeenCalledTimes(1);
    // And the button says so rather than only ignoring the press.
    expect(pending).toBeDisabled();
  });

  it("shows a 400 refusal exactly as the API worded it", async () => {
    addEvaluation.mockRejectedValue({
      detail: "no_misselling: needs agent_prompt, conversation, which a text run does not produce",
      statusCode: 400,
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    expect(
      await screen.findByText(
        "no_misselling: needs agent_prompt, conversation, which a text run does not produce",
      ),
    ).toBeInTheDocument();
  });

  // The contract says only *when* the add 409s (8 already selected, or no
  // run test yet) — it promises no sentence, so this test names no contracted
  // string: it proves the drawer echoes whatever body arrives, word for
  // word.
  it("shows the 409 body exactly as returned", async () => {
    addEvaluation.mockRejectedValue({
      detail: "Environment has no evaluations until it finishes building",
      statusCode: 409,
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    expect(
      await screen.findByText("Environment has no evaluations until it finishes building"),
    ).toBeInTheDocument();
  });

  it("shows 'Added' (disabled) for an eval already in `selected`", async () => {
    getHarnessEnvironment.mockResolvedValue({
      evaluations: { selected: [selectedEntry(NO_MISSELLING, "cfg-1")] },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    const button = await screen.findByRole("button", { name: /Added/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(addEvaluation).not.toHaveBeenCalled();
  });

  // `available` is this drawer's only active observer of that query, so
  // invalidating it on add would refetch it immediately — the server would
  // return the list minus what was just bound, and the row the user just
  // clicked would disappear. Environment mode has no receipt and no bound
  // group, so nothing on screen would say the add happened; if it was the
  // last offered eval the drawer would flip straight to the empty state.
  // The `boundEntries` filter alone (asserted below, "never lists the same
  // eval in both groups after a run-mode add") already closes the
  // duplicate-row bug an invalidation would be meant to fix.
  it("keeps the just-added row on screen, marked Added, after an add", async () => {
    // The server applies its own subtraction on every fetch: the second
    // `available` read no longer offers what was just bound. Since the add
    // must NOT trigger a same-tick refetch, only the first (constant)
    // mocked value is ever seen here — proving that against a mock that
    // WOULD expose a regression.
    getAvailableEvaluations.mockResolvedValueOnce({ evaluations: [NO_MISSELLING] });
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    addEvaluation.mockResolvedValue({
      evaluations: { selected: [selectedEntry(NO_MISSELLING, "cfg-1")] },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));

    // The only confirmation environment mode has.
    expect(await screen.findByRole("button", { name: /Added/i })).toBeDisabled();
    expect(screen.queryByText("Nothing left to add")).toBeNull();
  });

  it("warns and disables Add at the cap of 8", async () => {
    getHarnessEnvironment.mockResolvedValue({
      evaluations: {
        selected: Array.from({ length: 8 }, (_, i) =>
          selectedEntry({ ...NO_MISSELLING, name: `eval_${i}` }, `cfg-${i}`),
        ),
      },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByText(/maximum 8 evaluations/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add/i })).toBeDisabled();
  });

  // In run mode with an empty offer, the only rows on screen belong to the
  // bound group, whose own note says the cap does NOT gate it. The cap
  // warning must not sit above that group contradicting it — but it still
  // shows on the Evaluations tab, and in run mode when the offer is
  // non-empty, where it is accurate.
  it("suppresses the cap warning in run mode when the offer is empty (the cap doesn't gate the bound group)", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue({
      evaluations: {
        selected: Array.from({ length: 8 }, (_, i) =>
          selectedEntry({ ...NO_MISSELLING, name: `eval_${i}` }, `cfg-${i}`),
        ),
      },
    });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText("Already on this environment — grade this run's finished calls");
    expect(screen.queryByText(/maximum 8 evaluations/i)).toBeNull();
  });

  it("still shows the cap warning on the Evaluations tab (no run mode, no bound group)", async () => {
    getHarnessEnvironment.mockResolvedValue({
      evaluations: {
        selected: Array.from({ length: 8 }, (_, i) =>
          selectedEntry({ ...NO_MISSELLING, name: `eval_${i}` }, `cfg-${i}`),
        ),
      },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByText(/maximum 8 evaluations/i)).toBeInTheDocument();
  });

  it("shows the empty state when nothing is left to add, claiming no reason it cannot know", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(await screen.findByText(/Nothing left to add/i)).toBeInTheDocument();
    expect(
      screen.getByText("There's nothing this environment can be graded by right now."),
    ).toBeInTheDocument();
    // An empty list is a valid answer; it never says the reason is "already
    // applied", and an empty catalogue for this modality is the same
    // answer.
    expect(screen.queryByText(/is already applied/)).toBeNull();
  });

  it("shows the available list's own refusal, word for word", async () => {
    getAvailableEvaluations.mockRejectedValue({
      detail: "Environment has no evaluations until it finishes building",
      statusCode: 409,
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(
      await screen.findByText("Environment has no evaluations until it finishes building"),
    ).toBeInTheDocument();
    // The invented fallback is only for a failure with no body.
    expect(screen.queryByText(/Something went wrong fetching the library/)).toBeNull();
  });

  it("falls back to the generic sentence when the failure carries no detail", async () => {
    getAvailableEvaluations.mockRejectedValue({ statusCode: 500 });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(
      await screen.findByText("Something went wrong fetching the library. Try again."),
    ).toBeInTheDocument();
  });

  it("shows a refusal whose sentence arrives under `message` rather than `detail`", async () => {
    // Every one of this envelope's message fields is optional, so the
    // "shown exactly as returned" guarantee cannot hold for `detail` alone.
    addEvaluation.mockRejectedValue({
      message: "no_misselling: not an eval this environment can be graded by",
      statusCode: 400,
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));

    expect(
      await screen.findByText("no_misselling: not an eval this environment can be graded by"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Couldn’t add the evaluation/)).toBeNull();
  });

  // The applied list is what says which evals the environment already has.
  // Collapsing a failed read of it to "none" turns a fetch that never
  // answered into the confident claim that there is nothing to add.
  it("says the applied list could not be read, never 'Nothing left to add'", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    // A 404 is the one failure the detail query treats as terminal; every
    // other status is retried, which is the same branch reached a few
    // seconds later.
    getHarnessEnvironment.mockRejectedValue({
      detail: "No environment matches this id",
      statusCode: 404,
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByText("No environment matches this id")).toBeInTheDocument();
    expect(screen.queryByText("Nothing left to add")).toBeNull();
    expect(
      screen.queryByText("There's nothing this environment can be graded by right now."),
    ).toBeNull();
    // Nor a cap warning: how many are applied is exactly what isn't known.
    expect(screen.queryByText(/maximum 8 evaluations/i)).toBeNull();
  });

  // The applied list only says which of the offered rows are already on the
  // environment. When it fails, that is all that is lost: the offer list
  // answered, every row is addable, and hiding them would take away the one
  // thing the drawer is for over a fetch that says nothing about them. The
  // failure itself still needs to be visible and retryable, though — a strip
  // above the table carries the sentence and its own Retry.
  it("keeps the offered rows addable when only the applied list fails", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING] });
    getHarnessEnvironment.mockRejectedValue({
      detail: "No environment matches this id",
      statusCode: 404,
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    const addButton = screen.getByRole("button", { name: /^Add$/ });
    expect(addButton).toBeEnabled();
    // The empty state never stands in for the rows.
    expect(screen.queryByText("Nothing left to add")).toBeNull();
    // And nothing is claimed about what is already applied: no "Added" mark
    // on a row nobody has checked, and no cap.
    expect(screen.queryByRole("button", { name: /^Added$/ })).toBeNull();
    expect(screen.queryByText(/maximum 8 evaluations/i)).toBeNull();

    // The failed read still shows, in the strip above the table, with its
    // own Retry.
    expect(await screen.findByText("No environment matches this id")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(getHarnessEnvironment).toHaveBeenCalledTimes(2));
    // The offer list, which never failed, is not retried alongside it.
    expect(getAvailableEvaluations).toHaveBeenCalledTimes(1);

    fireEvent.click(addButton);
    await waitFor(() => expect(addEvaluation).toHaveBeenCalledWith("env-1", "no_misselling"));
  });

  // There is one Retry on screen for two reads. Retrying only the one whose
  // sentence is showing leaves the other failed, so the press looks like it
  // did nothing.
  it("retries both reads from the one Retry button when both failed", async () => {
    getAvailableEvaluations.mockRejectedValue({ statusCode: 500 });
    getHarnessEnvironment.mockRejectedValue({ statusCode: 404 });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    await screen.findByText("Something went wrong fetching the library. Try again.");
    expect(getAvailableEvaluations).toHaveBeenCalledTimes(1);
    expect(getHarnessEnvironment).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(getAvailableEvaluations).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getHarnessEnvironment).toHaveBeenCalledTimes(2));
  });

  it("shows the loading state, not the empty state, while the applied list is still being read", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText("Nothing left to add")).toBeNull();
    expect(
      screen.queryByText("There's nothing this environment can be graded by right now."),
    ).toBeNull();
  });

  // A loaded offer list is not held behind the applied list's own read: the
  // row shows as soon as the offer answers, addable, and only its "Added"
  // state waits on the applied list.
  it("shows the offered row rather than a spinner while the applied list is still pending", async () => {
    getHarnessEnvironment.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    const addButton = screen.getByRole("button", { name: /^Add$/ });
    expect(addButton).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^Added$/ })).toBeNull();
    expect(screen.queryByText(/maximum 8 evaluations/i)).toBeNull();
  });

  // The count on screen is the server's list length or nothing at all —
  // never the client store's idea of what was added.
  it("asserts no cap while the applied list is unknown", async () => {
    getHarnessEnvironment.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    await screen.findByRole("progressbar");
    expect(screen.queryByText(/maximum 8 evaluations/i)).toBeNull();
  });

  it("clears a stale add error once the drawer closes and reopens", async () => {
    addEvaluation.mockRejectedValue({
      detail: "no_misselling: needs agent_prompt, conversation, which a text run does not produce",
      statusCode: 400,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = (ui) => <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
    const onClose = vi.fn();
    const { rerender } = rtlRender(
      wrap(<AddEvaluationDrawer open env={ENV} onClose={onClose} />),
    );
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    expect(
      await screen.findByText(
        "no_misselling: needs agent_prompt, conversation, which a text run does not produce",
      ),
    ).toBeInTheDocument();

    // Close via the drawer's own close affordance — this is what resets the mutation.
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(wrap(<AddEvaluationDrawer open={false} env={ENV} onClose={onClose} />));
    rerender(wrap(<AddEvaluationDrawer open env={ENV} onClose={onClose} />));

    await screen.findByText("no_misselling");
    expect(
      screen.queryByText(
        "no_misselling: needs agent_prompt, conversation, which a text run does not produce",
      ),
    ).toBeNull();
  });

  // `SideDrawer` only hides the component, so an expanded row's state would
  // otherwise survive a close — reopening (even against a different
  // environment/run) would show it pre-expanded.
  it("collapses an expanded row when the drawer closes and reopens", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = (ui) => <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
    const onClose = vi.fn();
    const { rerender } = rtlRender(
      wrap(<AddEvaluationDrawer open env={ENV} onClose={onClose} />),
    );
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByText("no_misselling"));
    expect(screen.getByText(NO_MISSELLING.description)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(wrap(<AddEvaluationDrawer open={false} env={ENV} onClose={onClose} />));
    rerender(wrap(<AddEvaluationDrawer open env={ENV} onClose={onClose} />));

    await screen.findByText("no_misselling");
    expect(screen.queryByText(NO_MISSELLING.description)).toBeNull();
  });

  it("refetches the environment detail when the drawer closes after an add", async () => {
    addEvaluation.mockResolvedValue({ evaluations: { selected: [] } });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const wrap = (ui) => <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
    rtlRender(wrap(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />));
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));
    await waitFor(() => expect(addEvaluation).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["harness-environment", "env-1"],
      }),
    );
  });
});

describe("AddEvaluationDrawer — adding from inside a run", () => {
  it("calls the run-level endpoint and says what it queued", async () => {
    addRunEvaluation.mockResolvedValue({
      queued: 13,
      skipped_existing: 2,
      skipped_in_flight: 0,
      skipped_pending: 1,
      completed_calls: 16,
    });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    await waitFor(() =>
      expect(addRunEvaluation).toHaveBeenCalledWith("env-1", "ex-1", "no_misselling"),
    );
    // The environment-level add is not used from a run.
    expect(addEvaluation).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        "13 calls queued for grading, 2 already graded, 1 still being processed — of 16 calls that finished in this run.",
      ),
    ).toBeInTheDocument();
    // Grading is asynchronous and this drawer never polls — the receipt
    // says a reload is needed to see the new verdicts.
    expect(screen.getByText("Reload this run to see the new verdicts.")).toBeInTheDocument();
  });

  it("names how many finished calls the add will grade, before the click — no credit figure, and no claim that none of them is graded", async () => {
    render(
      <AddEvaluationDrawer
        open
        env={ENV}
        executionId="ex-1"
        completedCallsCount={16}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText("no_misselling");

    const sentence = screen.getByText(
      "Expand a row to see what fills each input. Each row below also grades this run's 16 finished calls — any that already have a verdict for it are left alone.",
    );
    expect(sentence).toBeInTheDocument();
    // No credit figure before the click — the sentence names only the
    // count, never a cost.
    expect(sentence.textContent).not.toMatch(/credit/i);
    // The old wording — "the 16 finished calls in this run THAT HAVE NO
    // VERDICT FOR IT YET" — was restrictive: it asserted all 16 lacked a
    // verdict, which the receipt routinely contradicts.
    expect(sentence.textContent).not.toMatch(/that have no verdict for it yet/);
  });

  it("names one finished call in the singular", async () => {
    render(
      <AddEvaluationDrawer
        open
        env={ENV}
        executionId="ex-1"
        completedCallsCount={1}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText("no_misselling");

    expect(
      screen.getByText(
        "Expand a row to see what fills each input. Each row below also grades this run's 1 finished call — any that already have a verdict for it are left alone.",
      ),
    ).toBeInTheDocument();
  });

  it("falls back to naming the calls without a number when the completed count isn't known", async () => {
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    expect(
      screen.getByText(
        "Expand a row to see what fills each input. Each row below also grades this run's finished calls — any that already have a verdict for it are left alone.",
      ),
    ).toBeInTheDocument();
  });

  it("says zero plainly once the completed count is known to be zero — never 'the 0 finished calls', never confused with 'not known yet'", async () => {
    render(
      <AddEvaluationDrawer
        open
        env={ENV}
        executionId="ex-1"
        completedCallsCount={0}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText("no_misselling");

    expect(
      screen.getByText(
        "Expand a row to see what fills each input. Nothing is graded yet: no call in this run has finished. An offered row is still added, and every call from here on is graded by it.",
      ),
    ).toBeInTheDocument();
    // Zero and "not known yet" are different answers and must read differently.
    expect(
      screen.queryByText(/also grades this run's finished calls/),
    ).toBeNull();
    // The add still binds the eval for future calls, so Add stays enabled.
    expect(screen.getByRole("button", { name: /^Add$/ })).toBeEnabled();
  });

  it("reads the row's cost chip as 'per call graded', not 'per run' — run mode only", async () => {
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    expect(screen.getByText("0.5 credits per call graded + judge tokens")).toBeInTheDocument();
    expect(screen.queryByText("0.5 credits per run + judge tokens")).toBeNull();
  });

  it("passes an add refusal through untouched — nothing was queued", async () => {
    addRunEvaluation.mockRejectedValue({
      detail: "no_misselling: not an eval this environment can be graded by",
      statusCode: 400,
    });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    expect(
      await screen.findByText("no_misselling: not an eval this environment can be graded by"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/queued for grading/)).toBeNull();
  });

  it("clears the queued-counts receipt once the drawer closes and reopens", async () => {
    addRunEvaluation.mockResolvedValue({
      queued: 13,
      skipped_existing: 2,
      skipped_in_flight: 0,
      skipped_pending: 1,
      completed_calls: 16,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = (ui) => <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
    const onClose = vi.fn();
    const { rerender } = rtlRender(
      wrap(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={onClose} />),
    );
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    expect(
      await screen.findByText(
        "13 calls queued for grading, 2 already graded, 1 still being processed — of 16 calls that finished in this run.",
      ),
    ).toBeInTheDocument();

    // Close via the drawer's own close affordance — this is what resets the mutation.
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(wrap(<AddEvaluationDrawer open={false} env={ENV} executionId="ex-1" onClose={onClose} />));
    rerender(wrap(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={onClose} />));

    await screen.findByText("no_misselling");
    expect(screen.queryByText(/queued for grading/)).toBeNull();
  });

  it("keeps the 202 receipt when the drawer is closed mid-flight, so a reopen after it settles still shows it", async () => {
    let resolveAdd;
    addRunEvaluation.mockReturnValue(
      new Promise((resolve) => {
        resolveAdd = resolve;
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = (ui) => <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
    const onClose = vi.fn();
    const { rerender } = rtlRender(
      wrap(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={onClose} />),
    );
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));
    // Wait for the pending state to actually land (react-query notifies
    // subscribers async) before closing, so the close genuinely races a
    // pending mutation rather than a stale pre-click render.
    await screen.findByText("…");

    // Close the drawer while the mutation is still pending — an
    // unconditional `mutation.reset()` here would detach the query-core
    // observer and blank the receipt even though the mutation keeps
    // running and the grading still happens.
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(
      wrap(<AddEvaluationDrawer open={false} env={ENV} executionId="ex-1" onClose={onClose} />),
    );

    // The 202 lands while the drawer is closed (but the component — and so
    // the mutation's observer — stays mounted; SideDrawer only hides it).
    resolveAdd({
      queued: 13,
      skipped_existing: 2,
      skipped_in_flight: 0,
      skipped_pending: 1,
      completed_calls: 16,
    });

    // Reopen: the receipt from the mid-flight add is still there once the
    // mutation has had a chance to settle.
    rerender(wrap(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={onClose} />));
    expect(
      await screen.findByText(
        "13 calls queued for grading, 2 already graded, 1 still being processed — of 16 calls that finished in this run.",
      ),
    ).toBeInTheDocument();
  });

  it("still shows a receipt when the 202 carries no body at all, and never claims 0 calls finished", async () => {
    // axios gives `""` for an empty 202 body; `{counts && …}` would render
    // nothing and the click would look like it had done nothing, although the
    // eval is bound and the grading queued.
    addRunEvaluation.mockResolvedValue("");
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));

    expect(
      await screen.findByText("Nothing new to grade — how many calls finished in this run isn't known."),
    ).toBeInTheDocument();
    expect(screen.getByText("Reload this run to see the new verdicts.")).toBeInTheDocument();
    // An empty body means the count is unknown, not zero — "of 0 calls"
    // would be a false claim that nothing finished.
    expect(screen.queryByText(/of 0 calls/)).toBeNull();
  });
});

describe("AddEvaluationDrawer — the environment's own evals, in run mode", () => {
  // The offer list subtracts already-selected evals, so an eval added from the
  // Evaluations tab is not in `available` at all, and nothing that had already
  // finished was graded by it. Without this group there is no control anywhere
  // that can grade those calls, and the run-level add's backfill path is
  // unreachable from the UI.
  const BOUND = { evaluations: { selected: [selectedEntry(NO_MISSELLING, "cfg-1")] } };
  const GROUP_TITLE = "Already on this environment — grade this run's finished calls";

  it("lists a bound eval as its own group and grades this run with it", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    addRunEvaluation.mockResolvedValue({
      queued: 13,
      skipped_existing: 2,
      skipped_in_flight: 0,
      skipped_pending: 1,
      completed_calls: 16,
    });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    // The group says what it is, and the eval is there even though `available`
    // is empty.
    expect(await screen.findByText(GROUP_TITLE)).toBeInTheDocument();
    expect(screen.getByText("no_misselling")).toBeInTheDocument();

    const grade = screen.getByRole("button", { name: "Grade this run" });
    expect(grade).toBeEnabled();
    fireEvent.click(grade);

    // The same run-level endpoint, with this bound eval's own name.
    await waitFor(() =>
      expect(addRunEvaluation).toHaveBeenCalledWith("env-1", "ex-1", "no_misselling"),
    );
    // Never the environment-level add: the eval is already bound.
    expect(addEvaluation).not.toHaveBeenCalled();
    // The same receipt an offered eval's add shows.
    expect(
      await screen.findByText(
        "13 calls queued for grading, 2 already graded, 1 still being processed — of 16 calls that finished in this run.",
      ),
    ).toBeInTheDocument();
  });

  // The common scenario: every eval this environment can be graded by was
  // already added from the Evaluations tab, so the offer list subtracts all of
  // them and `available` comes back empty. The old empty state ("There's nothing
  // this environment can be graded by right now") sat directly above the group
  // that contradicts it.
  it("shows a one-line note instead of the old empty state when the offer is empty but the bound group is not", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    expect(
      await screen.findByText("Every eval is already on this environment — grade this run below."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("There's nothing this environment can be graded by right now."),
    ).toBeNull();
    expect(screen.queryByText("Nothing left to add")).toBeNull();

    // The group is still there, with its Grade this run control.
    expect(screen.getByText(GROUP_TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Grade this run" })).toBeInTheDocument();
  });

  it("shows the same entry cells on a bound row, with the run-mode cost chip", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText(GROUP_TITLE);
    expect(screen.getByText("Library")).toBeInTheDocument();
    expect(screen.getByText("0.5 credits per call graded + judge tokens")).toBeInTheDocument();

    // The inputs are behind the same expander, with the API's labels.
    fireEvent.click(screen.getByRole("button", { name: "Expand no_misselling" }));
    expect(screen.getByText("{{conversation}}")).toBeInTheDocument();
    expect(screen.getByText("Call recording")).toBeInTheDocument();
    expect(screen.queryByText("voice_recording")).toBeNull();
  });

  it("disables Grade this run while a mutation is in flight", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    addRunEvaluation.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText(GROUP_TITLE);
    fireEvent.click(screen.getByRole("button", { name: "Grade this run" }));

    // The label becomes the pending marker and the button is disabled, so a
    // double click cannot issue a second POST.
    const pending = await screen.findByRole("button", { name: "…" });
    expect(pending).toBeDisabled();
    await waitFor(() => expect(addRunEvaluation).toHaveBeenCalledTimes(1));
  });

  it("passes a refusal on a bound eval through untouched", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    addRunEvaluation.mockRejectedValue({
      detail: "Run not found",
      statusCode: 404,
    });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText(GROUP_TITLE);
    fireEvent.click(screen.getByRole("button", { name: "Grade this run" }));

    expect(await screen.findByText("Run not found")).toBeInTheDocument();
    expect(screen.queryByText(/queued for grading/)).toBeNull();
  });

  // `BOUND` is a VOICE eval (NO_MISSELLING); the offer here must be voice
  // too — one environment has one `agent_type`, so it can never hold a
  // voice eval in `selected[]` while being offered a text one.
  it("offers the group alongside the offer list, not instead of it", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [LIBRARY_VOICE_EVAL] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText(GROUP_TITLE);
    // The offered eval keeps its Add; the bound one has Grade this run.
    expect(screen.getByText("off_topic_detection")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Add$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Grade this run" })).toBeInTheDocument();
  });

  it("never shows the group on the Evaluations tab — environment mode is unchanged", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [LIBRARY_VOICE_EVAL] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    await screen.findByText("off_topic_detection");
    expect(screen.queryByText(GROUP_TITLE)).toBeNull();
    expect(screen.queryByRole("button", { name: "Grade this run" })).toBeNull();
  });

  it("shows no group when the environment has no evals bound yet", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING] });
    getHarnessEnvironment.mockResolvedValue({ evaluations: { selected: [] } });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText("no_misselling");
    expect(screen.queryByText(GROUP_TITLE)).toBeNull();
  });

  // `boundEntries` filters `selected[]` by the offer list's current names. If
  // it did not, the same eval would render twice whenever both lists name it
  // — once as "Added" above, once as "Grade this run" below — which is
  // exactly what an environment-level add inside an open run-mode drawer
  // produces: the 201 body seeds `selected[]` while the offer list, not
  // refetched, still offers the row.
  it("never lists the same eval in both groups after an add inside an open drawer", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING] });
    getHarnessEnvironment.mockResolvedValue({ evaluations: { selected: [] } });
    addEvaluation.mockResolvedValue({
      evaluations: { selected: [selectedEntry(NO_MISSELLING, "cfg-1")] },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));
    await screen.findByRole("button", { name: /Added/i });

    // The eval renders exactly once, in exactly one of its three possible
    // states — never twice.
    expect(screen.getAllByText("no_misselling")).toHaveLength(1);
    expect(
      screen.getAllByRole("button", { name: /^(Add|Added|Grade this run)$/ }),
    ).toHaveLength(1);
  });

  // The run-level add's confirmation is its receipt, not an "Added" flip —
  // the 202 carries counts, not the detail, and nothing is refetched while
  // the drawer is open. What must never happen is the row appearing a second
  // time in the bound group below.
  it("leaves the offer row alone after a run-mode add and never doubles it into the bound group", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING] });
    getHarnessEnvironment.mockResolvedValue({ evaluations: { selected: [] } });
    addRunEvaluation.mockResolvedValue({
      queued: 3,
      skipped_existing: 0,
      skipped_in_flight: 0,
      skipped_pending: 1,
      completed_calls: 3,
    });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /^Add$/ }));

    await screen.findByText(/3 calls queued for grading/);
    expect(screen.getAllByText("no_misselling")).toHaveLength(1);
    expect(
      screen.getAllByRole("button", { name: /^(Add|Added|Grade this run)$/ }),
    ).toHaveLength(1);
  });

  // The group is deliberately rendered outside the offer list's
  // isLoading/isError/empty/list conditional — its data (`selected[]`)
  // comes from a different query, so a slow or failed offer fetch says
  // nothing about it.
  it("renders the bound group even while the offer list is still loading", async () => {
    getAvailableEvaluations.mockReturnValue(new Promise(() => {}));
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    expect(await screen.findByText(GROUP_TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Grade this run" })).toBeInTheDocument();
  });

  it("renders the bound group even when the offer list fails to load", async () => {
    getAvailableEvaluations.mockRejectedValue({
      detail: "Environment has no evaluations until it finishes building",
      statusCode: 409,
    });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    expect(
      await screen.findByText("Environment has no evaluations until it finishes building"),
    ).toBeInTheDocument();
    expect(screen.getByText(GROUP_TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Grade this run" })).toBeInTheDocument();
  });

  // The other direction: this group's OWN source is the environment detail.
  // A failed read of it empties the group silently, and — with an empty offer
  // above — the drawer would print "Nothing left to add" about a run that may
  // have eight evals to grade it with.
  it("says the bound group's source failed, rather than showing an empty drawer", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    // No body at all, so the generic sentence is what shows.
    getHarnessEnvironment.mockRejectedValue({ statusCode: 404 });
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    expect(
      await screen.findByText("Couldn’t load this environment's evaluations. Try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Nothing left to add")).toBeNull();
    expect(
      screen.queryByText("There's nothing this environment can be graded by right now."),
    ).toBeNull();
    // And no claim that everything is already on the environment either.
    expect(
      screen.queryByText("Every eval is already on this environment — grade this run below."),
    ).toBeNull();
  });

  it("shows the loading state while the bound group's source is still being read", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockReturnValue(new Promise(() => {}));
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText(GROUP_TITLE)).toBeNull();
    expect(screen.queryByText("Nothing left to add")).toBeNull();
    expect(
      screen.queryByText("There's nothing this environment can be graded by right now."),
    ).toBeNull();
  });
});
