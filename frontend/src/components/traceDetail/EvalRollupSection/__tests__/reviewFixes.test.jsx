import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { activatableProps } from "../utils";
import EvalRollupSection from "../index";

describe("activatableProps (keyboard parity)", () => {
  it("exposes button semantics and fires on Enter and Space", () => {
    const onActivate = vi.fn();
    const p = activatableProps(onActivate, { expanded: false });
    expect(p.role).toBe("button");
    expect(p.tabIndex).toBe(0);
    expect(p["aria-expanded"]).toBe(false);
    for (const key of ["Enter", " "]) {
      p.onKeyDown({ key, preventDefault: () => {} });
    }
    expect(onActivate).toHaveBeenCalledTimes(2);
  });

  it("other keys do not activate", () => {
    const onActivate = vi.fn();
    activatableProps(onActivate).onKeyDown({ key: "a", preventDefault: () => {} });
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("returns nothing when disabled, keeping it out of the tab order", () => {
    expect(activatableProps(() => {}, { enabled: false })).toEqual({});
  });

  it("omits aria-expanded for plain buttons", () => {
    expect(activatableProps(() => {})["aria-expanded"]).toBeUndefined();
  });
});

describe("failingEvals is not duplicated per failing span", () => {
  it("a numeric eval with 3 failing spans is reported once", () => {
    const onFix = vi.fn();
    const evalScores = {
      scope: "trace",
      eval_tasks: [
        {
          eval_task_id: "t1",
          eval_task_name: "QA",
          evals: [
            {
              eval_config_id: "c1",
              eval_name: "score-eval",
              output_type: "score",
              aggregate: 10,
              spans: [
                { span_id: "s1", span_name: "a", value: 10 },
                { span_id: "s2", span_name: "b", value: 20 },
                { span_id: "s3", span_name: "c", value: 30 },
              ],
            },
          ],
        },
      ],
    };
    render(<EvalRollupSection evalScores={evalScores} onFixWithFalcon={onFix} />);
    fireEvent.click(screen.getByText(/Fix with Falcon/i));
    expect(onFix).toHaveBeenCalledTimes(1);
    const { failingEvals, passed, total } = onFix.mock.calls[0][0];
    expect(total).toBe(3);                // all three spans counted
    expect(passed).toBe(0);               // all three below the cutoff
    expect(failingEvals).toHaveLength(1); // but the eval reported once
  });
});
