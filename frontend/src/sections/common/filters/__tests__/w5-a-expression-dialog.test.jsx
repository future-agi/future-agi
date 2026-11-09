// W5-1 A2 · the results door: examples from the runs in view, quick picks,
// the count line, errors and Apply.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import ExpressionFilterDialog from "../ExpressionFilterDialog";
import { countLine } from "../expressionDialogModel";
import {
  resultsCatalogForView,
  resultsExamples,
  resultsQuickPicks,
  valueHints,
} from "../resultsDoorModel";
import { suggest } from "../expressionSuggest";

const VIEW = {
  runs: [
    { run_id: "A", label: "#3A" },
    { run_id: "B", label: "#3B" },
  ],
  baselineId: "A",
  candidateId: "B",
  metrics: [
    {
      key: "eval:groundedness",
      name: "Groundedness",
      kind: "eval",
      output_type: "score",
      pass_threshold: 0.7,
    },
    {
      key: "eval:correctness",
      name: "Correctness",
      kind: "eval",
      output_type: "pass_fail",
    },
  ],
  metadataKeys: ["language"],
  splits: [{ id: "s1", name: "smoke", row_count: 20 }],
};
const ROWS = [
  {
    example_id: "e1",
    metadata: { language: "es" },
    inputs: { customer_message: "hola" },
    expected: { value: { expected_reply: "x" } },
    runs: { A: { cost: 0.001 }, B: { cost: 0.002 } },
  },
  {
    example_id: "e2",
    metadata: { language: "es" },
    inputs: { customer_message: "hi" },
    expected: { value: { expected_reply: "y" } },
    runs: { A: { cost: 0.004 }, B: { cost: 0.01 } },
  },
  {
    example_id: "e3",
    metadata: { language: "de" },
    inputs: { customer_message: "hallo" },
    expected: { value: { expected_reply: "z" } },
    runs: { A: { cost: 0.003 }, B: { cost: 0.002 } },
  },
];

describe("results door model", () => {
  it("builds examples from the runs and the primary pass/fail eval in view", () => {
    const ex = resultsExamples(VIEW, ROWS).map((e) => e.expr);
    expect(ex[0]).toBe("#3B.correctness.movement = regressed");
    expect(ex[1]).toBe("#3B.correctness < #3A.correctness");
    expect(ex[2]).toBe("not (all.output is empty)");
    expect(ex[3]).toMatch(/^any\.cost_usd > 0\.00\d+$/);
  });

  it("reads the cost example from R3 wire rows (per-run `results`)", () => {
    const wire = ROWS.map(({ runs, ...r }) => ({ ...r, results: runs }));
    const ex = resultsExamples(VIEW, wire).map((e) => e.expr);
    expect(ex[3]).toBe(resultsExamples(VIEW, ROWS)[3].expr);
  });

  it("offers Hide rows with no output and every-run-fails", () => {
    expect(resultsQuickPicks(VIEW)).toEqual([
      { label: "Hide rows with no output", expr: "not (all.output is empty)" },
      {
        label: "Only rows every run fails on Correctness",
        expr: "all.correctness = fail",
      },
    ]);
  });

  it("counts metadata values in the rows for autocomplete", () => {
    const hints = valueHints(VIEW, ROWS);
    expect(hints.values["metadata.language"]).toEqual([
      { value: "es", count: 2 },
      { value: "de", count: 1 },
    ]);
    const cat = resultsCatalogForView(VIEW, ROWS);
    const s = suggest("metadata.language = ", 20, cat, hints);
    expect(s.items.slice(0, 2).map((i) => [i.label, i.detail])).toEqual([
      ["es", "2 rows"],
      ["de", "1 rows"],
    ]);
    expect(suggest("#3", 2, cat, hints).items.map((i) => i.label)).toEqual([
      "#3A",
      "#3B",
    ]);
    expect(suggest("#3B.corr", 8, cat, hints).items[0].label).toBe(
      "#3B.correctness",
    );
    expect(
      suggest("customer_message ", 17, cat, hints).items.map((i) => i.label),
    ).toContain("contains");
  });

  it("formats the count line", () => {
    expect(countLine({ matched: 14, total: 248 })).toBe(
      "Matches 14 of 248 rows",
    );
    expect(countLine({ matched: 0, total: 248 })).toBe("Matches no rows");
  });
});

describe("ExpressionFilterDialog", () => {
  const catalog = resultsCatalogForView(VIEW, ROWS);

  it("counts after typing, applies, and blocks Apply on an error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const count = vi.fn(async (expr) =>
      expr.startsWith("lang")
        ? { error: "Unknown field “lang”. Did you mean metadata.language?" }
        : { matched: 2, total: 3 },
    );
    const onApply = vi.fn();
    render(
      <ExpressionFilterDialog
        open
        onClose={() => {}}
        onApply={onApply}
        catalog={catalog}
        count={count}
        examples={resultsExamples(VIEW, ROWS)}
        quickPicks={resultsQuickPicks(VIEW)}
      />,
    );
    expect(screen.getByText("Filter by expression")).toBeTruthy();
    const input = screen.getByTestId("expression-input");
    // L7c g1 (expr:results): no fixed expression naming runs or evals a
    // page may not have; each door passes its own first example.
    expect(input.getAttribute("placeholder")).toBe("Type an expression");
    fireEvent.change(input, { target: { value: 'metadata.language = "es"' } });
    await act(async () => {
      vi.advanceTimersByTime(350);
    });
    await waitFor(() =>
      expect(screen.getByTestId("expression-count").textContent).toBe(
        "Matches 2 of 3 rows",
      ),
    );
    expect(count).toHaveBeenCalledWith('metadata.language = "es"');
    fireEvent.click(screen.getByTestId("expression-apply"));
    expect(onApply).toHaveBeenCalledWith('metadata.language = "es"');

    fireEvent.change(input, { target: { value: 'lang = "es"' } });
    await act(async () => {
      vi.advanceTimersByTime(350);
    });
    await waitFor(() =>
      expect(screen.getByTestId("expression-error").textContent).toBe(
        "Unknown field “lang”. Did you mean metadata.language?",
      ),
    );
    expect(screen.getByTestId("expression-apply").disabled).toBe(true);
    expect(screen.getByTestId("expression-error-span").textContent).toBe(
      "lang",
    );

    fireEvent.change(input, { target: { value: "(a = 1" } });
    expect(screen.getByTestId("expression-error").textContent).toBe(
      "Missing “)”.",
    );
    expect(screen.getByTestId("expression-apply").disabled).toBe(true);

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getAllByTestId("expression-quick-pick")[0]);
    expect(input.value).toBe("not (all.output is empty)");
    fireEvent.click(screen.getAllByTestId("expression-example")[1]);
    expect(input.value).toBe("#3B.correctness < #3A.correctness");
    vi.useRealTimers();
  });
});
