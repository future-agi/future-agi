import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import EvaluateCell from "../EvaluateCell";

// The normalization module imports the grid; keep that unrelated UI out of this unit test.
vi.mock("src/sections/common/DevelopCellRenderer/CustomCellRender", () => ({
  default: () => null,
}));

describe("EvaluateCell unit", () => {
  it.each([
    [0, "0"],
    [0.75, "0.75"],
  ])("renders the SCORE result %s without treating zero as empty", (value, text) => {
    render(
      <EvaluateCell
        value={value}
        dataType="float"
        originType="evaluation"
        cellData={{ valueInfos: { output: "score", data: { result: value } } }}
      />,
    );

    expect(screen.getByText(text, { exact: true })).toBeVisible();
  });

  it.each([
    [0, "0%"],
    [0.75, "75%"],
  ])("renders the stored score %s when data.result is absent", (value, text) => {
    render(
      <EvaluateCell
        value={value}
        dataType="float"
        originType="evaluation"
        cellData={{ valueInfos: { output: "score", data: {} } }}
      />,
    );

    expect(screen.getByText(text, { exact: true })).toBeVisible();
  });

  it.each([
    ["missing data", { output: "score" }],
    ["null result", { output: "score", data: { result: null } }],
    ["empty result", { output: "score", data: { result: "" } }],
  ])("renders the stored score with %s", (_description, valueInfos) => {
    render(
      <EvaluateCell
        value={0.75}
        dataType="float"
        originType="evaluation"
        cellData={{ valueInfos }}
      />,
    );

    expect(screen.getByText("75%", { exact: true })).toBeVisible();
  });

  it("uses the column SCORE output when the cell only has reason metadata", () => {
    render(
      <EvaluateCell
        value={0.75}
        dataType="float"
        originType="evaluation"
        outputType="score"
        cellData={{ value_infos: { reason: "The answer is mostly relevant." } }}
      />,
    );

    expect(screen.getByText("75%", { exact: true })).toBeVisible();
  });

  it.each(["Passed", "Failed"])("renders a %s pass/fail result", (value) => {
    render(
      <EvaluateCell
        value={value}
        dataType="boolean"
        originType="evaluation"
        cellData={{ valueInfos: { output: "pass/fail", data: { result: value } } }}
      />,
    );

    expect(screen.getByText(value, { exact: true })).toBeVisible();
  });
});
