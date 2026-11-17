import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CorrectionsOption from "../CorrectionsOption";
import { queueMetadata } from "../reviewModel";

// W4 C1: Start review › "Reviewers can correct the expected answer".
let columns = [];
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/itemHooks",
  () => ({ useDatasetColumns: () => ({ data: columns }) }),
);

const COLS = [
  { id: "c-in", name: "customer_message", role: "input" },
  { id: "c-exp", name: "expected_reply", role: "expected" },
  { id: "c-old", name: "old_expected", role: "expected", deleted: true },
  { id: "c-exp2", name: "expected_tool_calls", role: "expected" },
];

describe("CorrectionsOption", () => {
  beforeEach(() => {
    columns = COLS;
  });

  it("starts off on the dataset's first expected-answer column", () => {
    const onChange = vi.fn();
    render(
      <CorrectionsOption datasetId="d1" value={null} onChange={onChange} />,
    );
    expect(onChange).toHaveBeenCalledWith({
      on: false,
      column_id: "c-exp",
      column_name: "expected_reply",
    });
  });

  it("the switch turns it on; then the column picker and help line show", () => {
    const onChange = vi.fn();
    const value = {
      on: false,
      column_id: "c-exp",
      column_name: "expected_reply",
    };
    const { rerender } = render(
      <CorrectionsOption datasetId="d1" value={value} onChange={onChange} />,
    );
    expect(
      screen.getByText("Reviewers can correct the expected answer"),
    ).toBeTruthy();
    expect(screen.queryByText(/Each correction is suggested/)).toBeNull();
    fireEvent.click(
      screen.getByLabelText("Reviewers can correct the expected answer"),
    );
    expect(onChange).toHaveBeenCalledWith({ ...value, on: true });
    rerender(
      <CorrectionsOption
        datasetId="d1"
        value={{ ...value, on: true }}
        onChange={onChange}
      />,
    );
    expect(
      screen.getByText(
        "Each correction is suggested to the dataset; you choose which ones to save as a new version.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("expected_reply")).toBeTruthy();
  });

  it("is absent when the dataset has no expected-answer column", () => {
    columns = COLS.filter((c) => c.role !== "expected");
    const onChange = vi.fn();
    render(
      <CorrectionsOption datasetId="d1" value={null} onChange={onChange} />,
    );
    expect(screen.queryByTestId("corrections-option")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("the queue metadata carries the column only when on", () => {
    const base = { mode: "rubric", runId: "r1", datasetId: "d1" };
    expect(
      queueMetadata({ ...base, corrections: { on: false, column_id: "c-exp" } })
        .corrections,
    ).toBeUndefined();
    expect(
      queueMetadata({
        ...base,
        corrections: {
          on: true,
          column_id: "c-exp",
          column_name: "expected_reply",
        },
      }).corrections,
    ).toEqual({ column_id: "c-exp", column_name: "expected_reply" });
    expect(
      queueMetadata({
        mode: "pairwise",
        runA: "a",
        runB: "b",
        datasetId: "d1",
        corrections: {
          on: true,
          column_id: "c-exp",
          column_name: "expected_reply",
        },
      }).corrections,
    ).toEqual({ column_id: "c-exp", column_name: "expected_reply" });
  });
});
