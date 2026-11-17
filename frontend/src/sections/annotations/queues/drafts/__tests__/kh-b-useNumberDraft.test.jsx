// KH-SCOPE §6 B1 / mount M-B3: a typed number reaches the score only when it
// fits the label's rule; the reason shows inline and nothing is clamped.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useNumberDraft } from "../useNumberDraft";
import NumberRule from "../NumberRule";
import { useEntryRefusals } from "../useEntryRefusals";

function Field({ initial, settings, onCommit }) {
  const [value, setValue] = useState(initial);
  const entry = useNumberDraft({
    name: "Helpfulness",
    settings,
    value,
    onChange: (v) => {
      setValue(v);
      onCommit(v);
    },
  });
  return (
    <>
      <input
        aria-label="Helpfulness"
        type="number"
        value={entry.text}
        onChange={entry.onChange}
        onBlur={entry.onBlur}
      />
      <NumberRule entry={entry} />
      <button type="button" onClick={() => setValue(0.9)}>
        slide
      </button>
      <output data-testid="score">{String(value)}</output>
    </>
  );
}
Field.propTypes = {
  initial: PropTypes.number,
  settings: PropTypes.object,
  onCommit: PropTypes.func,
};

const HELP = { min: 0, max: 1, step_size: 0.1 };
const setup = (initial = 0.5, settings = HELP) => {
  const onCommit = vi.fn();
  render(<Field initial={initial} settings={settings} onCommit={onCommit} />);
  return {
    onCommit,
    input: screen.getByLabelText("Helpfulness"),
    rule: () => screen.getByTestId("numeric-rule"),
    score: () => screen.getByTestId("score").textContent,
  };
};

describe("useNumberDraft + NumberRule", () => {
  it("shows the rule under the field", () => {
    const { rule, input } = setup();
    expect(rule()).toHaveTextContent("0 – 1 · steps of 0.1");
    expect(input).toHaveValue(0.5);
  });

  it("refuses 1.4 inline and keeps the previous value", () => {
    const { input, rule, score, onCommit } = setup();
    fireEvent.change(input, { target: { value: "1.4" } });
    expect(rule()).toHaveTextContent(
      "Helpfulness must be between 0 and 1 (you entered 1.4).",
    );
    expect(rule()).toHaveAttribute("role", "alert");
    expect(score()).toBe("0.5");
    expect(onCommit).not.toHaveBeenCalled();
    // Leaving the field shows the kept value again; the reason stays.
    fireEvent.blur(input);
    expect(input).toHaveValue(0.5);
    expect(rule()).toHaveTextContent("(you entered 1.4)");
  });

  it("-0.2 and blur: the reason, never a silent clamp to the minimum", () => {
    const { input, rule, score, onCommit } = setup();
    fireEvent.change(input, { target: { value: "-0.2" } });
    fireEvent.blur(input);
    expect(rule()).toHaveTextContent(
      "Helpfulness must be between 0 and 1 (you entered -0.2).",
    );
    expect(score()).toBe("0.5");
    expect(onCommit).not.toHaveBeenCalledWith(0);
    expect(input).toHaveValue(0.5);
  });

  it("refuses an off-step number and accepts the next valid one", () => {
    const { input, rule, score, onCommit } = setup();
    fireEvent.change(input, { target: { value: "0.35" } });
    expect(rule()).toHaveTextContent(
      "Helpfulness moves in steps of 0.1 (you entered 0.35).",
    );
    fireEvent.change(input, { target: { value: "0.3" } });
    expect(onCommit).toHaveBeenLastCalledWith(0.3);
    expect(score()).toBe("0.3");
    expect(rule()).toHaveTextContent("0 – 1 · steps of 0.1");
  });

  it("lets a number pass through a refused prefix (min 5: typing 10)", () => {
    const { input, rule, onCommit } = setup(7, { min: 5, max: 10 });
    fireEvent.change(input, { target: { value: "1" } });
    expect(input).toHaveValue(1); // what was typed stays visible
    expect(rule()).toHaveTextContent("(you entered 1)");
    fireEvent.change(input, { target: { value: "10" } });
    expect(onCommit).toHaveBeenLastCalledWith(10);
    expect(rule()).toHaveTextContent("5 – 10 · steps of 1");
  });

  it("an emptied field clears the score; a value set elsewhere replaces the field", () => {
    const { input, rule, score, onCommit } = setup();
    fireEvent.change(input, { target: { value: "1.4" } });
    fireEvent.click(screen.getByRole("button", { name: "slide" }));
    expect(input).toHaveValue(0.9);
    expect(rule()).toHaveTextContent("0 – 1 · steps of 0.1");
    fireEvent.change(input, { target: { value: "" } });
    expect(onCommit).toHaveBeenLastCalledWith(null);
    expect(score()).toBe("null");
  });

  it("reports a stored value outside the rule on blur instead of changing it", () => {
    const { input, rule, score } = setup(3);
    expect(input).toHaveValue(3);
    fireEvent.blur(input);
    expect(rule()).toHaveTextContent(
      "Helpfulness must be between 0 and 1 (you entered 3).",
    );
    expect(score()).toBe("3");
  });
});

// The form's view of a field (KH B1 must-fix): the refusal stays pending
// until the reviewer enters a number the rule accepts, so Submit and ⌘↵
// refuse instead of saving the kept score.
function Form({ initial, settings }) {
  const [value, setValue] = useState(initial);
  const refusals = useEntryRefusals();
  const entry = useNumberDraft({
    name: "Helpfulness",
    settings,
    value,
    onChange: setValue,
    entryRef: refusals.refFor("l-help"),
  });
  const [seen, setSeen] = useState(null);
  return (
    <>
      <input
        aria-label="Helpfulness"
        type="number"
        value={entry.text}
        onChange={entry.onChange}
        onBlur={entry.onBlur}
      />
      <button type="button" onClick={() => setValue(0.9)}>
        slide
      </button>
      <button
        type="button"
        onClick={() => setSeen(JSON.stringify(refusals.pending()))}
      >
        check
      </button>
      <output data-testid="pending">{seen}</output>
    </>
  );
}
Form.propTypes = { initial: PropTypes.number, settings: PropTypes.object };

describe("useNumberDraft + useEntryRefusals: the pending refusal", () => {
  const setupForm = (initial = 0.5) => {
    render(<Form initial={initial} settings={HELP} />);
    const check = () => {
      fireEvent.click(screen.getByRole("button", { name: "check" }));
      return JSON.parse(screen.getByTestId("pending").textContent);
    };
    return { input: screen.getByLabelText("Helpfulness"), check };
  };
  const RANGE = {
    label_id: null,
    name: "Helpfulness",
    kind: "range",
    text: "Helpfulness must be between 0 and 1 (you entered 1.4).",
  };

  it("none while the field holds an accepted number", () => {
    const { input, check } = setupForm();
    expect(check()).toEqual({});
    fireEvent.change(input, { target: { value: "0.7" } });
    expect(check()).toEqual({});
  });

  it("a refused entry is pending while typed and after the field loses focus", () => {
    const { input, check } = setupForm();
    fireEvent.change(input, { target: { value: "1.4" } });
    expect(check()).toEqual({ "l-help": RANGE });
    fireEvent.blur(input);
    expect(input).toHaveValue(0.5);
    expect(check()).toEqual({ "l-help": RANGE });
  });

  it("an accepted entry, or a score set elsewhere, clears it", () => {
    const { input, check } = setupForm();
    fireEvent.change(input, { target: { value: "0.35" } });
    expect(check()["l-help"]).toMatchObject({ kind: "step" });
    fireEvent.change(input, { target: { value: "0.5" } });
    expect(check()).toEqual({});
    fireEvent.change(input, { target: { value: "-0.2" } });
    expect(check()["l-help"]).toMatchObject({ kind: "range" });
    fireEvent.click(screen.getByRole("button", { name: "slide" }));
    expect(input).toHaveValue(0.9);
    expect(check()).toEqual({});
  });

  it("a half-typed number is pending, and blur shows why", () => {
    const { input, check } = setupForm();
    Object.defineProperty(input, "validity", {
      configurable: true,
      get: () => ({ badInput: true }),
    });
    fireEvent.change(input, { target: { value: "" } });
    expect(check()).toEqual({
      "l-help": {
        label_id: null,
        name: "Helpfulness",
        kind: "range",
        text: "Helpfulness must be between 0 and 1.",
      },
    });
    fireEvent.blur(input);
    expect(input).toHaveValue(0.5);
    expect(check()["l-help"]).toMatchObject({
      text: "Helpfulness must be between 0 and 1.",
    });
  });
});
