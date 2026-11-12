import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import AgreementTab from "../AgreementTab";
import { sourceLine } from "../agreementModel";

// Keep the host, source list, report, disagreements and link builders real.
// Only unrelated advanced-panel reads are isolated from this stored-GET test.
const state = vi.hoisted(() => ({ data: null, params: null, report: null }));
vi.mock("../alignmentApi", () => ({
  hasAlignmentRoutes: () => true,
  useAgreement: (_templateId, params) => {
    state.params = params;
    return { data: state.data, isError: false, refetch: vi.fn() };
  },
}));
/* eslint-disable react/prop-types */
vi.mock("../ComparePanel", () => ({
  default: ({ report }) => {
    state.report = report;
    return <div data-testid="compare-control">Compare with another judge</div>;
  },
}));
vi.mock("../ImproveJudgePanel", () => ({
  default: ({ mode }) => <div data-testid="improve-control">{mode}</div>,
}));
vi.mock("../TrainJudgePanel", () => ({
  default: ({ mode }) => <div data-testid="train-control">{mode}</div>,
}));
vi.mock("../CheckedVersions", () => ({ default: () => null }));
vi.mock("../ConfidencePanel", () => ({ default: () => null }));
/* eslint-enable react/prop-types */

const DATASET = "10000000-0000-4000-8000-000000000001";
const BINDING = "20000000-0000-4000-8000-000000000001";
const OTHER_BINDING = "20000000-0000-4000-8000-000000000002";
const LABEL = "30000000-0000-4000-8000-000000000001";
const ROW = "40000000-0000-4000-8000-000000000001";
const OTHER_ROW = "40000000-0000-4000-8000-000000000002";
const KEY = `${BINDING}:${LABEL}`;
const OTHER_KEY = `${OTHER_BINDING}:${LABEL}`;
const source = {
  key: KEY,
  dataset_name: "Saved data",
  binding_name: "Correctness",
  label_name: "Human review",
};
const otherSource = { ...source, key: OTHER_KEY, binding_name: "Relevance" };

// EvalAgreementResponse.result: discovery metadata has no counts or IDs;
// the selected report carries flat identifiers and scalar human/judge values.
const storedResult = () => ({
  sources: [otherSource, source],
  report: {
    ...source,
    binding_id: BINDING,
    dataset_id: DATASET,
    label_id: LABEL,
    label_type: "categorical",
    n: 4,
    numeric: false,
    accuracy: 0.75,
    kappa: 0.5,
    mad: null,
    spearman: null,
    labels: ["Fail", "Pass"],
    matrix: [[1, 1], [0, 2]],
    per_label: [
      { label: "Fail", precision: 1, recall: 0.5, f1: 2 / 3, support: 2 },
      { label: "Pass", precision: 2 / 3, recall: 1, f1: 0.8, support: 2 },
    ],
    excluded: 1,
    disagreements: [{ row_id: ROW, human: "Fail", judge: "Pass" }],
  },
});
const theme = createTheme({
  palette: { green: { 500: "#008000" }, red: { 500: "#ff0000" } },
});
const Location = () => {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
};
const mount = (search = "") =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/dashboard/evaluations/template-a${search}`]}>
        <AgreementTab templateId="template-a" evalData={{ name: "Correctness" }} />
        <Location />
      </MemoryRouter>
    </ThemeProvider>,
  );
const selectedButton = (name) =>
  within(screen.getByTestId("w7-sources")).getByRole("button", { name });
const currentUrl = () =>
  new URL(screen.getByTestId("location").textContent, "https://example.test");

beforeEach(() => {
  state.data = storedResult();
  state.params = null;
  state.report = null;
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: ["evals.agreement", "evals.agreementTools"],
  });
});
afterEach(() => setLaunchOverrideForTest(null));

describe("show-all Agreement with the stored-report contract", () => {
  it("selects the report key and distinguishes bindings without inventing counts", () => {
    mount();
    expect(selectedButton("Saved data · Correctness · Human review"))
      .toHaveClass("Mui-selected");
    expect(selectedButton("Saved data · Relevance · Human review"))
      .not.toHaveClass("Mui-selected");
    expect(screen.getByTestId("w7-sources")).not.toHaveTextContent(/undefined|null|rows/);
    expect(screen.getByTestId("w7-headline")).toHaveTextContent(
      "Accuracy 75% · κ 0.50 · n 4",
    );
    expect(screen.getByTestId("compare-control")).toBeInTheDocument();
    expect(screen.getByTestId("improve-control")).toBeInTheDocument();
    expect(screen.getByTestId("train-control")).toBeInTheDocument();
  });

  it("uses the selected source for requests and links even with a different deep link", () => {
    mount("?dataset=old-dataset&label=old-label");
    expect(state.params).toMatchObject({
      source: null, dataset_id: "old-dataset", label_id: "old-label",
    });
    fireEvent.click(selectedButton("Saved data · Correctness · Human review"));
    expect(state.params).toEqual({
      source: KEY, dataset_id: null, label_id: null, include: "pairs",
    });
    fireEvent.click(screen.getByRole("button", { name: ROW }));
    const url = currentUrl();
    expect(url.pathname).toBe(`/dashboard/develop/${DATASET}`);
    expect(url.searchParams.get("tab")).toBe("data");
    expect(JSON.parse(url.searchParams.get("filters"))).toEqual([{
      column_id: "system:row_ids",
      filter_config: {
        filter_type: "categorical", filter_op: "in", filter_value: [ROW], label: ROW,
      },
    }]);
  });

  it("renders the row UUID and scalar verdicts with no made-up output or notes", () => {
    mount();
    const row = within(screen.getByTestId(`w7-dis-${ROW}`));
    expect(row.getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      ROW, "–", "Pass", "Fail",
    ]);
    expect(state.report.source_key).toBe(KEY);
    expect(state.report.source).toMatchObject({
      dataset_id: DATASET, binding_id: BINDING, label_id: LABEL,
    });
    expect(state.report.disagreements[0]).toEqual({
      row_id: ROW, row_label: ROW,
      human: { verdict: "Fail" }, judge: { verdict: "Pass" },
    });
    for (const field of ["pairs", "labelled_rows", "stored_vs_rescored", "judge_model"])
      expect(state.report).not.toHaveProperty(field);
    // The capped disagreements are not the all-pairs population. In particular,
    // even a nonzero matrix cell cannot open rows absent from the response.
    expect(screen.getByTestId("w7-cell-Pass-Pass")).toBeDisabled();
    expect(screen.getByTestId("w7-cell-Fail-Pass")).toBeDisabled();
    expect(state.data).toEqual(storedResult());
  });

  it("shows false and zero instead of blank cells and scopes Open rows to both UUIDs", () => {
    state.data.report.disagreements = [
      { row_id: ROW, human: false, judge: 0 },
      { row_id: OTHER_ROW, human: 0, judge: false },
    ];
    mount();
    expect(within(screen.getByTestId(`w7-dis-${ROW}`))
      .getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      ROW, "–", "0", "false",
    ]);
    expect(within(screen.getByTestId(`w7-dis-${OTHER_ROW}`))
      .getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      OTHER_ROW, "–", "false", "0",
    ]);
    fireEvent.click(screen.getByTestId("w7-open-disagreements"));
    const url = currentUrl();
    expect(url.pathname).toBe(`/dashboard/develop/${DATASET}`);
    expect(JSON.parse(url.searchParams.get("filters"))).toEqual([{
      column_id: "system:row_ids",
      filter_config: {
        filter_type: "categorical", filter_op: "in",
        filter_value: [ROW, OTHER_ROW], label: "judge ≠ reviewers",
      },
    }]);
  });

  it("renders a numeric report without prototype-only fields", () => {
    state.data.report = {
      ...state.data.report, label_type: "numeric", numeric: true, n: 1,
      accuracy: null, kappa: null, spearman: null, mad: 0,
      labels: [], matrix: [], per_label: [], disagreements: [],
    };
    mount();
    expect(selectedButton("Saved data · Correctness · Human review"))
      .toHaveClass("Mui-selected");
    expect(screen.getByTestId("w7-numeric-headline")).toHaveTextContent(
      "Spearman ρ – · mean difference 0.00 · n 1",
    );
    expect(screen.getByTestId("w7-sources")).not.toHaveTextContent(/undefined|null|rows/);
    expect(screen.queryByTestId("w7-agreement-matrix")).toBeNull();
  });

  it("preserves enriched reports and existing advanced panel inputs", () => {
    const enriched = {
      ...state.data.report,
      source_key: KEY,
      source: { dataset_id: DATASET, label_id: LABEL },
      disagreements: [{
        row_id: ROW, row_label: "Example 1", output: "Saved output",
        human: { verdict: "Fail", note: "A supplied review", by: "Reviewer" },
        judge: { verdict: "Pass", reason: "A supplied reason" },
      }],
      labelled_rows: [ROW],
      stored_vs_rescored: 1,
    };
    state.data = {
      sources: [source], report: enriched, improve_mode: "improve",
      pairs: enriched.disagreements,
    };
    mount();
    expect(state.report).toBe(enriched);
    expect(screen.getByTestId("w7-dis-Example 1")).toHaveTextContent("Saved output");
    expect(screen.getByTestId("w7-dis-Example 1")).toHaveTextContent("A supplied review");
    expect(screen.getByTestId("improve-control")).toHaveTextContent("improve");
    expect(screen.getByTestId("train-control")).toHaveTextContent("improve");
    expect(screen.getByTestId("w7-cell-Fail-Pass")).not.toBeDisabled();
  });

  it("keeps discovery available when there is no selected report", () => {
    state.data.report = null;
    mount();
    fireEvent.click(selectedButton("Saved data · Correctness · Human review"));
    expect(state.params.source).toBe(KEY);
    expect(screen.queryByTestId("w7-headline")).toBeNull();
    expect(screen.getByTestId("w7-sources")).not.toHaveTextContent("undefined");
  });
});

describe("source metadata", () => {
  it("omits absent counts but retains an explicit zero, singular and optional statistics", () => {
    expect(sourceLine(source)).toBe("Saved data · Correctness · Human review");
    expect(sourceLine({ ...source, n: null })).toBe(sourceLine(source));
    expect(sourceLine({ ...source, n: 0 })).toBe(`${sourceLine(source)} · 0 rows`);
    expect(sourceLine({ ...source, n: 1 })).toBe(`${sourceLine(source)} · 1 row`);
    expect(sourceLine({ ...source, n: 4, accuracy: 0.75, kappa: 0.5 }))
      .toBe(`${sourceLine(source)} · 4 rows · 75% · κ 0.50`);
    expect(sourceLine({ run_label: "Run A", label_name: "Review", n: 2, spearman: 0 }))
      .toBe("Run A · Review · 2 rows · ρ 0.00");
  });
});
