import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import EvalsStep from "../EvalsStep";
import { EVALS_COPY } from "../evals.constants";

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
function Harness({ initial, onGo, patchSpy }) {
  const [envState, setEnvState] = useState(initial);
  const patch = (p) => {
    patchSpy?.(p);
    setEnvState((s) => ({ ...s, ...p }));
  };
  return <EvalsStep env={ENV} envState={envState} patch={patch} onGo={onGo} />;
}

beforeEach(() => {
  picker.calls.length = 0;
});

describe("EvalsStep — Suggested / Added split", () => {
  it("shows Suggested = preset minus applied and 'Add all N' adds them", () => {
    const patchSpy = vi.fn();
    render(
      <Harness
        initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "policy_adherence" }] }}
        patchSpy={patchSpy}
      />
    );

    // preset has 3, one applied → 2 suggested.
    expect(screen.getByText("Suggested evaluations (2)")).toBeInTheDocument();
    expect(screen.getByText("Added evaluations (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add all 2" }));

    expect(patchSpy).toHaveBeenCalledTimes(1);
    const { evals } = patchSpy.mock.calls[0][0];
    expect(evals).toHaveLength(3);
    expect(evals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "policy_adherence" }),
        expect.objectContaining({ id: "task_success" }),
        expect.objectContaining({ id: "pii_leakage" }),
      ])
    );

    // Nothing left to suggest → the Suggested card disappears.
    expect(screen.queryByText(/Suggested evaluations/)).not.toBeInTheDocument();
    expect(screen.getByText("Added evaluations (3)")).toBeInTheDocument();
  });

  it("removing an added eval returns it to Suggested", () => {
    const patchSpy = vi.fn();
    render(
      <Harness
        initial={{ scenarios: [{ id: "s1" }], evals: [{ id: "task_success" }] }}
        patchSpy={patchSpy}
      />
    );

    expect(screen.getByText("Suggested evaluations (2)")).toBeInTheDocument();
    expect(screen.getByText("Added evaluations (1)")).toBeInTheDocument();
    // task_success is applied, so it only appears in the Added list.
    expect(screen.getByText("Task success")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.remove }));

    expect(patchSpy).toHaveBeenCalledWith({ evals: [] });
    // Back to all three suggested, none added.
    expect(screen.getByText("Suggested evaluations (3)")).toBeInTheDocument();
    expect(screen.getByText("Added evaluations (0)")).toBeInTheDocument();
  });
});

describe("EvalsStep — locked when scenarios are missing", () => {
  it("routes the empty state to onGo('scenarios') and never patches", () => {
    const patchSpy = vi.fn();
    const onGo = vi.fn();
    render(
      <Harness initial={{ scenarios: [], evals: [] }} onGo={onGo} patchSpy={patchSpy} />
    );

    // No Suggested card while locked.
    expect(screen.queryByText(/Suggested evaluations/)).not.toBeInTheDocument();
    expect(screen.getByText(EVALS_COPY.lockedTitle)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.addScenarios }));

    expect(onGo).toHaveBeenCalledWith("scenarios");
    expect(patchSpy).not.toHaveBeenCalled();
  });
});

describe("EvalsStep — AddEvalsDrawer over the product picker", () => {
  it("flows each saved eval through onAdd and keeps the drawer open", () => {
    const patchSpy = vi.fn();
    render(
      <Harness
        initial={{ scenarios: [{ id: "s1" }], evals: [], agent: { typeId: "voice" } }}
        patchSpy={patchSpy}
      />
    );

    // Empty state (no preset) → open the picker from its Add button.
    fireEvent.click(screen.getByRole("button", { name: EVALS_COPY.add }));
    expect(screen.getByTestId("eval-picker")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "save-a" }));
    expect(patchSpy).toHaveBeenCalledTimes(1);
    expect(patchSpy.mock.calls[0][0].evals).toEqual([
      expect.objectContaining({ id: "a", custom: true }),
    ]);
    // Wrapper does not close the drawer — the product's keepOpenAfterSave keeps it.
    expect(screen.getByTestId("eval-picker")).toBeInTheDocument();

    const afterFirst = picker.calls.at(-1);
    expect(afterFirst.keepOpenAfterSave).toBe(true);
    expect(afterFirst.existingEvals).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "a" })])
    );

    fireEvent.click(screen.getByRole("button", { name: "save-b" }));
    expect(patchSpy).toHaveBeenCalledTimes(2);
    expect(patchSpy.mock.calls[1][0].evals).toEqual([
      expect.objectContaining({ id: "a" }),
      expect.objectContaining({ id: "b", custom: true }),
    ]);

    // Re-saving an already-added eval (the drawer stays on its list) is a no-op:
    // useAppliedEvals filters by id, so no extra patch.
    fireEvent.click(screen.getByRole("button", { name: "save-a" }));
    expect(patchSpy).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: "picker-close" }));
    expect(screen.queryByTestId("eval-picker")).not.toBeInTheDocument();
  });
});
