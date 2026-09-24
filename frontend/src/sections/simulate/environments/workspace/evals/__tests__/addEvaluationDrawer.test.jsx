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

describe("AddEvaluationDrawer — the row (§1 entry, P24, P25, F1)", () => {
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
    // P1: `label` is the only text shown for a source.
    expect(screen.queryByText("voice_recording")).toBeNull();
    expect(screen.queryByText("agent_prompt")).toBeNull();
    // Read-only: nothing to edit.
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("pairs a key with its label by key, never by the order of required_keys (P1)", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING_UNALIGNED] });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByText("no_misselling"));

    const rows = screen.getAllByText(/^\{\{.+\}\}$/).map((el) => el.textContent);
    // The order on screen is `inputs`' (sorted by key), not required_keys'.
    expect(rows).toEqual(["{{agent_prompt}}", "{{conversation}}"]);
  });

  // P3: every entry in ONE `available` response carries the same `agent_type`,
  // so all three here are `"text"` — mixing a voice entry in would build a
  // response the server cannot produce (round-3 L5).
  it("shows Library/Custom and the cost line, built from the two fields (P25)", async () => {
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

  it("gives the row's expander an accessible name and announces its state (L8)", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    // Minor-2 (fix round 1): the name carries the eval so two rows (offer and
    // bound) never announce as identical "Expand" buttons.
    const expander = screen.getByRole("button", { name: "Expand no_misselling" });
    expect(expander).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(expander);

    const collapse = screen.getByRole("button", { name: "Collapse no_misselling" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Call recording")).toBeInTheDocument();
  });
});

describe("AddEvaluationDrawer — adding to the environment (§3)", () => {
  it("posts the name and nothing else", async () => {
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    await waitFor(() =>
      expect(addEvaluation).toHaveBeenCalledWith("env-1", "no_misselling"),
    );
    expect(addEvaluation).toHaveBeenCalledTimes(1);
  });

  it("shows a 400 refusal exactly as the API worded it (P9)", async () => {
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

  // §3/P10 says only *when* the add 409s (8 already selected, or no run test
  // yet) — it promises no sentence, so this test names no contracted string: it
  // proves the drawer echoes whatever body arrives, word for word.
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

  it("shows 'Added' (disabled) for an eval already in `selected` (P29)", async () => {
    getHarnessEnvironment.mockResolvedValue({
      evaluations: { selected: [selectedEntry(NO_MISSELLING, "cfg-1")] },
    });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);

    const button = await screen.findByRole("button", { name: /Added/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(addEvaluation).not.toHaveBeenCalled();
  });

  // Important-1 (fix round 2): round 1 added an `available` invalidation to
  // `useAddEvaluation` that refetches the offer list in the same tick as the
  // add. `available` is this drawer's only active observer of that query, so
  // the refetch is immediate — the server applies P6 and returns the list
  // minus what was just bound, and the row the user just clicked disappears.
  // Environment mode has no receipt and no bound group, so nothing on screen
  // said the add happened; if it was the last offered eval the drawer flipped
  // straight to the empty state. The `boundEntries` filter alone (asserted
  // below, "never lists the same eval in both groups after a run-mode add")
  // already closes the duplicate-row bug this invalidation was meant to fix,
  // so the invalidation itself was reverted.
  it("keeps the just-added row on screen, marked Added, after a §3 add (P29)", async () => {
    // The server applies P6 on every fetch: the second `available` read no
    // longer offers what was just bound. Since the add must NOT trigger a
    // same-tick refetch, only the first (constant) mocked value is ever seen
    // here — proving that against a mock that WOULD expose a regression.
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

  // L1 (round 4): in run mode with an empty offer, the only rows on screen
  // belong to the bound group, whose own note says the cap does NOT gate it
  // (P27 v1.7). The cap warning must not sit above that group contradicting
  // it — but it still shows on the Evaluations tab, and in run mode when the
  // offer is non-empty, where it is accurate.
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

  it("shows the empty state when nothing is left to add, claiming no reason it cannot know (P6, L10)", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    render(<AddEvaluationDrawer open env={ENV} onClose={vi.fn()} />);
    expect(await screen.findByText(/Nothing left to add/i)).toBeInTheDocument();
    expect(
      screen.getByText("There's nothing this environment can be graded by right now."),
    ).toBeInTheDocument();
    // P6 says an empty list is a valid answer; it never says the reason is
    // "already applied", and an empty catalogue for this modality is the same
    // answer.
    expect(screen.queryByText(/is already applied/)).toBeNull();
  });

  it("shows the available list's own refusal, word for word (§2 P7)", async () => {
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

  // L7 (round 4): `SideDrawer` only hides the component, so an expanded row's
  // state used to survive a close — reopening (even against a different
  // environment/run) showed it pre-expanded. Prove by removal: dropping the
  // `setExpanded(null)` line from `handleClose` makes this fail, because the
  // detail row would still be visible after the reopen.
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

  it("refetches the environment detail when the drawer closes after an add (P29, L11)", async () => {
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

describe("AddEvaluationDrawer — adding from inside a run (§6, P27)", () => {
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
    // L3 (round 2): grading is asynchronous and this drawer never polls (Q4) —
    // the receipt says a reload is needed to see the new verdicts.
    expect(screen.getByText("Reload this run to see the new verdicts.")).toBeInTheDocument();
  });

  it("names how many finished calls the add will grade, before the click — no credit figure, and no claim that none of them is graded (P27; L3)", async () => {
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
    // No credit figure before the click (owner's rule, 2026-09-23 night) — the
    // sentence names only the count, never a cost.
    expect(sentence.textContent).not.toMatch(/credit/i);
    // L3 (round 3): the old wording — "the 16 finished calls in this run THAT
    // HAVE NO VERDICT FOR IT YET" — was restrictive, i.e. it asserted all 16
    // lacked a verdict, which the receipt routinely contradicts.
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

  it("says zero plainly once the completed count is known to be zero — never 'the 0 finished calls', never confused with 'not known yet' (L2)", async () => {
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
    // The add still binds the eval for future calls, so Add stays enabled
    // (round-3 L2's own remedy: wording only, no behaviour change).
    expect(screen.getByRole("button", { name: /^Add$/ })).toBeEnabled();
  });

  it("reads the row's cost chip as 'per call graded', not 'per run' — run mode only (P25)", async () => {
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);
    await screen.findByText("no_misselling");

    expect(screen.getByText("0.5 credits per call graded + judge tokens")).toBeInTheDocument();
    expect(screen.queryByText("0.5 credits per run + judge tokens")).toBeNull();
  });

  it("passes a §3 refusal through untouched — nothing was queued (P18)", async () => {
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

  it("keeps the 202 receipt when the drawer is closed mid-flight, so a reopen after it settles still shows it (L5)", async () => {
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

    // Close the drawer while the mutation is still pending — the old code
    // called `mutation.reset()` unconditionally here, which detaches the
    // query-core observer and would blank the receipt even though the
    // mutation keeps running and the grading still happens (L5).
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

  it("still shows a receipt when the 202 carries no body at all, and never claims 0 calls finished (L6, Minor-1)", async () => {
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
    // Minor-1 (fix round 1): an empty body means the count is unknown, not
    // zero — "of 0 calls" would be a false claim that nothing finished.
    expect(screen.queryByText(/of 0 calls/)).toBeNull();
  });
});

describe("AddEvaluationDrawer — the environment's own evals, in run mode (§6, P27 v1.7)", () => {
  // Round-3 M3: §2 subtracts already-selected evals (P6), so an eval added from
  // the Evaluations tab is not in `available` at all — and P11 means nothing
  // that had already finished was graded by it. Without this group there is no
  // control anywhere that can grade those calls, and §6's backfill path, P20's
  // skip and P22's repeat rule are all unreachable from the UI.
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

    // The same run-level endpoint, with this bound eval's own name (§6).
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

  // Important-2 (fix round 2): P27 v1.7's own primary scenario — every eval
  // this environment can be graded by was already added from the Evaluations
  // tab, so §2/P6 subtracts all of them and `available` comes back empty.
  // The old empty state ("There's nothing this environment can be graded by
  // right now") sat directly above the group that contradicts it.
  it("shows a one-line note instead of the old empty state when the offer is empty but the bound group is not (Important-2)", async () => {
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

  it("shows the same entry cells on a bound row, with the run-mode cost chip (P24, P25)", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    await screen.findByText(GROUP_TITLE);
    expect(screen.getByText("Library")).toBeInTheDocument();
    expect(screen.getByText("0.5 credits per call graded + judge tokens")).toBeInTheDocument();

    // The inputs are behind the same expander, with the API's labels (P1, F1).
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

  it("passes a refusal on a bound eval through untouched (P18)", async () => {
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

  // Minor-6 (fix round 2): `BOUND` is a VOICE eval (NO_MISSELLING); the offer
  // here must be voice too — one environment has one `agent_type`, so it can
  // never hold a voice eval in `selected[]` while being offered a text one
  // (the same P3 impossibility L5 fixed one level down, for one `available`
  // response).
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

  it("never shows the group on the Evaluations tab — environment mode is unchanged (P6)", async () => {
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

  // Important-1 (fix round 1): a run-mode add refetches `selected[]`
  // (`useAddRunEvaluation` invalidates the detail) without necessarily having
  // refetched `available` in the same tick — the offer row is deliberately
  // left in place marked "Added". Before the fix, `boundEntries` was every
  // `selected` entry unconditionally, so the same eval rendered twice: once
  // as "Added" above, once as "Grade this run" below.
  it("never lists the same eval in both groups after a run-mode add", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [NO_MISSELLING] });
    getHarnessEnvironment.mockResolvedValueOnce({ evaluations: { selected: [] } });
    getHarnessEnvironment.mockResolvedValue({
      evaluations: { selected: [selectedEntry(NO_MISSELLING, "cfg-1")] },
    });
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
    await screen.findByRole("button", { name: /Added/i });

    // Minor-5 (fix round 1): the eval renders exactly once, in exactly one of
    // its three possible states — never twice. This holds regardless of
    // whether `available` has caught up to the add yet: an ordinary
    // `toBeNull()` on "Grade this run" is only true because this test's
    // `available` mock is a constant that never applies P6's subtraction;
    // against a real server the post-add refetch (on the drawer's next open)
    // would legitimately drop the eval into the bound group instead, and
    // "Grade this run" would correctly appear. Asserting the total count
    // across all three action labels holds in either ordering.
    expect(screen.getAllByText("no_misselling")).toHaveLength(1);
    expect(
      screen.getAllByRole("button", { name: /^(Add|Added|Grade this run)$/ }),
    ).toHaveLength(1);
  });

  // Minor-4 (fix round 1): the group is deliberately rendered outside the
  // offer list's isLoading/isError/empty/list conditional — its data
  // (`selected[]`) comes from a different query, so a slow or failed offer
  // fetch says nothing about it.
  it("renders the bound group even while the offer list is still loading (Minor-4)", async () => {
    getAvailableEvaluations.mockReturnValue(new Promise(() => {}));
    getHarnessEnvironment.mockResolvedValue(BOUND);
    render(<AddEvaluationDrawer open env={ENV} executionId="ex-1" onClose={vi.fn()} />);

    expect(await screen.findByText(GROUP_TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Grade this run" })).toBeInTheDocument();
  });

  it("renders the bound group even when the offer list fails to load (Minor-4)", async () => {
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
});
