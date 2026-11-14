import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { generateEvalColumnsFromConfig } from "../helper";
import CallLogsHeaderCellRenderer from "../CallLogs/CallLogsHeaderCellRenderer";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const passFail = {
  id: "helpful",
  name: "Task completed",
  output_type: "Pass/Fail",
  target_type: "span",
  is_visible: true,
};
const choices = {
  id: "tone",
  name: "Caller tone",
  output_type: "choices",
  target_type: "trace",
  choices_map: { neutral: "neutral" },
  is_visible: true,
};

const renderCell = (column, evalOutput) => {
  const params = {
    data: { eval_outputs: { [column.field.split(".")[1]]: evalOutput } },
    colDef: column,
  };
  params.value = column.valueGetter(params);
  const Cell = column.cellRenderer;
  return render(<Cell {...params} />);
};

describe("voice call-log eval columns", () => {
  it("render count tiles like the Observe lists", () => {
    const [passFailColumn, choiceColumn] = generateEvalColumnsFromConfig([
      passFail,
      choices,
    ]);

    renderCell(passFailColumn, {
      name: "Task completed",
      output_type: "Pass/Fail",
      output: 0,
      counts: { pass: 0, fail: 1 },
    });
    expect(screen.getByText("1 fail")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();

    renderCell(choiceColumn, {
      name: "Caller tone",
      output_type: "str_list",
      output: ["apologetic"],
      counts: { neutral: 0, apologetic: 1 },
    });
    expect(screen.getByText("1 apologetic")).toBeInTheDocument();
  });

  it("keeps the existing rendering for rows without counts", () => {
    const [passFailColumn] = generateEvalColumnsFromConfig([passFail]);

    renderCell(passFailColumn, {
      name: "Task completed",
      output_type: "Pass/Fail",
      output: 100,
    });
    expect(screen.queryByText(/pass$/)).not.toBeInTheDocument();
    expect(screen.getByText(/100/)).toBeInTheDocument();
  });

  it("pass the eval target to the header glyph", () => {
    const [passFailColumn, choiceColumn] = generateEvalColumnsFromConfig([
      passFail,
      choices,
    ]);
    expect(passFailColumn.headerComponentParams.targetType).toBe("span");
    expect(choiceColumn.headerComponentParams.targetType).toBe("trace");

    const { unmount } = render(
      <CallLogsHeaderCellRenderer
        displayName="Task completed"
        targetType="span"
      />,
    );
    expect(screen.getByLabelText("Span evaluation")).toHaveTextContent("S");
    unmount();

    render(<CallLogsHeaderCellRenderer displayName="Duration" />);
    expect(screen.queryByLabelText(/evaluation/)).not.toBeInTheDocument();
  });
});
