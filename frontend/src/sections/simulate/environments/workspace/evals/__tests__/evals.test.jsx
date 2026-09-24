import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useState } from "react";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EvalsStep from "../EvalsStep";
import { EVALS_COPY } from "../evals.constants";
import { NO_MISSELLING, selectedEntry } from "./fixtures/evalEntries";

// §4 client, mocked so a backed-env remove exercises the real DELETE path
// without the apiPath contract throw.
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  getHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(() => Promise.resolve()),
  getAvailableEvaluations: vi.fn(() => Promise.resolve({ evaluations: [] })),
  addEvaluation: vi.fn(),
  addRunEvaluation: vi.fn(),
}));
const { deleteAppliedEvaluation, getHarnessEnvironment } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);

// EvalsStep now uses a react-query mutation (§4 remove-eval), so every render
// needs a client. Wrap the library render once so the call sites stay unchanged.
const render = (ui, options) =>
  rtlRender(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
    options,
  );

// The product eval picker is a large real drawer with its own data fetching;
// stub it with a marker that exposes the callbacks the wrapper wires up.
const picker = vi.hoisted(() => ({ calls: [] }));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: (p) => {
    picker.calls.push(p);
    return p.open ? (
      <div data-testid="eval-picker">
        <button type="button" onClick={() => p.onEvalAdded({ templateId: "a", name: "A", mapping: {} })}>
          save-a
        </button>
        <button type="button" onClick={() => p.onEvalAdded({ templateId: "b", name: "B", mapping: {} })}>
          save-b
        </button>
        <button type="button" onClick={p.onClose}>
          picker-close
        </button>
      </div>
    ) : null;
  },
}));

const ENV = {
  id: "env-1",
  name: "Support",
  surface: "voice",
  evalPreset: ["task_success", "policy_adherence", "pii_leakage"],
};

// eslint-disable-next-line react/prop-types
function Harness({ env = ENV, initial, onGo, patchSpy, backed = false }) {
  const [envState, setEnvState] = useState(initial);
  const patch = (p) => {
    patchSpy?.(p);
    setEnvState((s) => ({ ...s, ...p }));
  };
  return <EvalsStep env={env} envState={envState} patch={patch} onGo={onGo} backed={backed} />;
}

beforeEach(() => {
  deleteAppliedEvaluation.mockClear();
  picker.calls.length = 0;
});

describe("EvalsStep — tool-call evaluation toggle", () => {
  it("keeps the toggle disabled until an agent is connected", () => {
    render(<Harness initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "e1" }] }} />);
    expect(screen.getByRole("checkbox", { name: EVALS_COPY.toolCall.title })).toBeDisabled();
  });

  it("enables it with an agent and patches toolCallEval on flip", () => {
    const patchSpy = vi.fn();
    render(
      <Harness
        initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "e1" }], agent: { typeId: "voice" } }}
        patchSpy={patchSpy}
      />,
    );
    const toggle = screen.getByRole("checkbox", { name: EVALS_COPY.toolCall.title });
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    expect(patchSpy).toHaveBeenCalledWith({ toolCallEval: true });
  });
});

describe("EvalsStep — preset auto-seeds into Added (no Suggested card)", () => {
  it("seeds the whole preset into Added on first empty mount", () => {
    const patchSpy = vi.fn();
    render(<Harness initial={{ scenarios: [{ id: "s1" }], evals: [] }} patchSpy={patchSpy} />);

    // The preset was added for the user — no separate Suggested card to click.
    expect(screen.queryByText(/Suggested evaluations/)).not.toBeInTheDocument();
    expect(patchSpy).toHaveBeenCalledTimes(1);
    const { evals } = patchSpy.mock.calls[0][0];
    expect(evals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "task_success" }),
        expect.objectContaining({ id: "policy_adherence" }),
        expect.objectContaining({ id: "pii_leakage" }),
      ]),
    );
    expect(screen.getByText("Added evaluations (3)")).toBeInTheDocument();
  });

  it("does not seed when evals already exist", () => {
    const patchSpy = vi.fn();
    render(
      <Harness initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "policy_adherence" }] }} patchSpy={patchSpy} />,
    );

    // Already has one → the seed is skipped, and there is no Suggested card.
    expect(patchSpy).not.toHaveBeenCalled();
    expect(screen.queryByText(/Suggested evaluations/)).not.toBeInTheDocument();
    expect(screen.getByText("Added evaluations (1)")).toBeInTheDocument();
  });

  it("does not re-seed after the user removes an added eval", () => {
    const patchSpy = vi.fn();
    render(
      <Harness initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "task_success" }] }} patchSpy={patchSpy} />,
    );

    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.remove }));

    expect(patchSpy).toHaveBeenCalledWith({ evals: [] });
    // The ref-guard keeps the suggestions from looping straight back in.
    expect(screen.getByText("Added evaluations (0)")).toBeInTheDocument();
    expect(screen.getByText(EVALS_COPY.emptyTitle)).toBeInTheDocument();
  });
});

describe("EvalsStep — locked when scenarios are missing", () => {
  it("does not seed, and routes the empty state to onGo('scenarios')", () => {
    const patchSpy = vi.fn();
    const onGo = vi.fn();
    render(<Harness initial={{ scenarios: [], evals: [] }} onGo={onGo} patchSpy={patchSpy} />);

    expect(screen.queryByText(/Suggested evaluations/)).not.toBeInTheDocument();
    expect(screen.getByText(EVALS_COPY.lockedTitle)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.addScenarios }));

    expect(onGo).toHaveBeenCalledWith("scenarios");
    // No auto-seed while scenarios are missing.
    expect(patchSpy).not.toHaveBeenCalled();
  });
});

describe("EvalsStep — AddEvalsDrawer over the product picker", () => {
  // An env with no preset so nothing auto-seeds — this test is about the picker.
  const noPreset = { ...ENV, evalPreset: [] };

  it("flows each saved eval through onAdd and closes the drawer on a single add", () => {
    const patchSpy = vi.fn();
    render(
      <Harness
        env={noPreset}
        initial={{ scenarios: [{ id: "s1" }], evals: [], agent: { typeId: "voice" } }}
        patchSpy={patchSpy}
      />,
    );

    // Empty state (no preset) → open the picker from its Add button.
    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.add }));
    expect(screen.getByTestId("eval-picker")).toBeInTheDocument();

    // A single, non-queued add maps that eval and closes the drawer.
    fireEvent.click(screen.getByRole("button", { name: "save-a" }));
    expect(patchSpy).toHaveBeenCalledTimes(1);
    expect(patchSpy.mock.calls[0][0].evals).toEqual([
      expect.objectContaining({ id: "a", custom: true }),
    ]);
    expect(screen.queryByTestId("eval-picker")).not.toBeInTheDocument();

    // Single-add mode: the picker is told not to keep itself open.
    const afterFirst = picker.calls.at(-1);
    expect(afterFirst.keepOpenAfterSave).toBe(false);

    // Re-open to add a second eval — the first flows back in as existing.
    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.add }));
    const reopened = picker.calls.at(-1);
    expect(reopened.existingEvals).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "a" })]),
    );

    fireEvent.click(screen.getByRole("button", { name: "save-b" }));
    expect(patchSpy).toHaveBeenCalledTimes(2);
    expect(patchSpy.mock.calls[1][0].evals).toEqual([
      expect.objectContaining({ id: "a" }),
      expect.objectContaining({ id: "b", custom: true }),
    ]);
    expect(screen.queryByTestId("eval-picker")).not.toBeInTheDocument();
  });
});

describe("EvalsStep — template lock (read-only until forked)", () => {
  const LOCK_TOOLTIP = "Fork this environment to edit.";
  // One applied eval so the header Add and the per-row remove both render.
  const lockedState = { scenarios: [{ id: "s1" }], evals: [{ id: "policy_adherence" }] };

  const renderLocked = (locked) =>
    render(<EvalsStep env={ENV} envState={lockedState} patch={vi.fn()} onGo={vi.fn()} locked={locked} />);

  it("disables the add/remove controls with the fork tooltip", () => {
    renderLocked(true);
    expect(screen.getByRole("button", { name: EVALS_COPY.add })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: EVALS_COPY.remove })[0]).toBeDisabled();
    expect(screen.getAllByLabelText(LOCK_TOOLTIP).length).toBeGreaterThan(0);
  });

  it("keeps the same controls enabled when not locked", () => {
    renderLocked(false);
    expect(screen.getByRole("button", { name: EVALS_COPY.add })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: EVALS_COPY.remove })[0]).toBeEnabled();
    expect(screen.queryByLabelText(LOCK_TOOLTIP)).toBeNull();
  });
});

describe("EvalsStep — §4 remove on a backend-backed env", () => {
  // A backed env's applied set comes from §5 detail (evaluations.selected), not
  // the store — so mock the detail fetch to supply the real selected row.
  const detailWith = (selected) => ({ evaluations: { selected } });
  const state = { scenarios: [{ id: "s1" }], evals: [] };

  beforeEach(() => {
    getHarnessEnvironment.mockResolvedValue(
      detailWith([selectedEntry(NO_MISSELLING, "cfg-1")]),
    );
  });

  // Minor-3 (fix round 1): the module mock's default is
  // `deleteAppliedEvaluation: vi.fn(() => Promise.resolve())`, but the
  // top-level `beforeEach` only `mockClear()`s it — that clears call history,
  // not an implementation override. The L4 test below sets
  // `mockRejectedValue`, which is a standing override that survives
  // `mockClear()`; left in place it would fail the next test appended after
  // it with a rejected DELETE it never asked for. `mockRestore()` undoes both
  // the override and the call history, back to the `vi.fn(() => …)` given at
  // mock-factory time.
  afterEach(() => {
    deleteAppliedEvaluation.mockRestore();
  });

  it("shows where the eval came from, whether it costs, and what fills its inputs (P25)", async () => {
    render(<Harness backed initial={state} patchSpy={vi.fn()} />);

    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    expect(screen.getByText("Library")).toBeInTheDocument();
    expect(screen.getByText("0.5 credits per run + judge tokens")).toBeInTheDocument();
    expect(screen.getByText("{{conversation}}")).toBeInTheDocument();
    expect(screen.getByText("Call recording")).toBeInTheDocument();
    // P1: the raw source never reaches the screen.
    expect(screen.queryByText("voice_recording")).toBeNull();
  });

  it("lists the §5 selected evals and fires the real DELETE with the config id", async () => {
    render(<Harness backed initial={state} patchSpy={vi.fn()} />);

    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: EVALS_COPY.remove })[0]);

    await waitFor(() =>
      expect(deleteAppliedEvaluation).toHaveBeenCalledWith("env-1", "cfg-1"),
    );
  });

  it("stays store-only (no DELETE) when the env is not backend-backed", () => {
    const patchSpy = vi.fn();
    render(
      <Harness
        initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "task_success", name: "Task success" }] }}
        patchSpy={patchSpy}
      />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: EVALS_COPY.remove })[0]);

    expect(deleteAppliedEvaluation).not.toHaveBeenCalled();
    expect(patchSpy).toHaveBeenCalledWith(expect.objectContaining({ evals: [] }));
  });

  it("disables remove while a backed env is still building", async () => {
    render(
      <Harness backed env={{ ...ENV, buildStatus: "building" }} initial={state} patchSpy={vi.fn()} />,
    );
    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: EVALS_COPY.remove })[0]).toBeDisabled();
  });

  it("shows the server's sentence when a remove is refused, attributed to the eval it failed for, and keeps the row (L4, Minor-5)", async () => {
    deleteAppliedEvaluation.mockRejectedValue({
      detail: "Environment has no evaluations until it finishes building",
      statusCode: 409,
    });
    render(<Harness backed initial={state} patchSpy={vi.fn()} />);

    await screen.findByText("no_misselling");
    fireEvent.click(screen.getAllByRole("button", { name: EVALS_COPY.remove })[0]);

    // Minor-5 (fix round 1): the Alert names which row the refusal belongs to,
    // not just the server's sentence — with several rows a bare "already
    // removed" doesn't say which one.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "no_misselling: Environment has no evaluations until it finishes building",
    );
    // Nothing was removed, so the row is still there — the Alert is the only
    // thing that changed.
    expect(screen.getByText("no_misselling")).toBeInTheDocument();
  });
});
