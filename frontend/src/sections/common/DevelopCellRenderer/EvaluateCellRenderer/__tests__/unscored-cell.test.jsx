import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import CustomCellRender from "../../CustomCellRender";
import EvaluateCell from "../EvaluateCell";
import NumericCell from "../NumericCell";

// Keep the real grid dispatcher, evaluation wrapper, score/numeric renderers,
// running skeleton and error renderer. Unrelated grid surfaces stay isolated.
vi.mock("../../RenderMeta", () => ({ default: () => null }));
vi.mock("../EvaluateArrayCellRenderer", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/states", () => ({
  useCompositeEvalStore: () => vi.fn(),
}));
// This module imports the entire dataset grid. Supply its documented result
// contract explicitly per case rather than duplicating its parsing algorithm.
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalResult: vi.fn(),
}));
vi.mock("src/utils/utils", () => ({
  interpolateColorBasedOnScore: vi.fn(() => "transparent"),
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ getQueriesData: () => [] }),
}));
vi.mock("src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext", () => ({
  useSingleImageViewContext: () => ({ setImageUrl: vi.fn() }),
}));
vi.mock("src/hooks/use-row-hover", () => ({
  useRowHover: () => ({ isRowHovered: false, cellRef: null }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true }),
}));
vi.mock("src/utils/constants", () => ({
  ORIGIN_OF_COLUMNS: { EXPERIMENT: "experiment" },
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("src/sections/develop-detail/DataTab/DataGrid/CellChrome", () => ({
  default: ({ children }) => <>{children}</>,
}));
/* eslint-enable react/prop-types */

vi.mock("../../CellRenderers/FloatIntegerCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/DatetimeCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/TextArrayJsonCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/ImageCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/ImagesCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/AudioCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/AnnotationArrayCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/JsonCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/FileCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/PersonaCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/ConversationCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/ChecksCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/TraceItemCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/EnumCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/VideoCellRenderer", () => ({ default: () => null }));
vi.mock("../../CellRenderers/MediaCellRenderer", () => ({
  default: () => null,
  mediaCellKind: () => null,
}));
vi.mock("../../../CellMarkdown", () => ({ default: () => null }));
vi.mock("../../../GenerateDiffText", () => ({ default: () => null }));
vi.mock("../../../EvaluationReasonFallback", () => ({ default: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(normalizeEvalResult).mockReturnValue({ kind: "empty" });
});
afterEach(cleanup);

const absentValues = [
  ["null", null],
  ["undefined", undefined],
  ["empty string", ""],
  ["spaces", "   "],
  ["tabs and newlines", "\t\n"],
  ["non-breaking space", " "],
];

const evaluationProps = (value, outputType = "score") => ({
  value,
  outputType,
  dataType: "float",
  originType: "evaluation",
  isFutureAgiEval: true,
  cellData: { cell_value: value, status: "pass", value_infos: [] },
});

const gridProps = (outputType, status = "pass", value = null) => {
  const colDef = {
    dataType: "float",
    originType: "evaluation",
    col: {
      id: "score-column",
      dataType: "float",
      originType: "evaluation",
      output_type: outputType,
      status: "Completed",
      eval_tag: ["futureagi"],
    },
  };
  return {
    value,
    colDef,
    column: { colId: "score-column", colDef },
    data: {
      row_id: "appended-row",
      "score-column": { cell_value: value, status, value_infos: [] },
    },
    node: {},
    versioned: true,
    datasetId: "dataset",
  };
};

const expectBlank = (container) => {
  expect(container.textContent).toBe("");
  expect(screen.queryByText(/^0%?$/)).not.toBeInTheDocument();
  expect(interpolateColorBasedOnScore).not.toHaveBeenCalled();
};

describe("Unscored evaluation cells", () => {
  it.each(absentValues)("R1 score leaves %s blank", (_label, value) => {
    const { container } = render(<EvaluateCell {...evaluationProps(value)} />);
    expectBlank(container);
  });

  it.each(absentValues)("R2 numeric output leaves %s blank", (_label, value) => {
    const { container } = render(
      <EvaluateCell {...evaluationProps(value, "numeric")} />,
    );
    expectBlank(container);
  });

  it("R3 honors a normalized empty array instead of coercing it to zero", () => {
    const { container } = render(<EvaluateCell {...evaluationProps([])} />);
    expectBlank(container);
  });

  it.each(["score", "numeric"])(
    "R4 completed-column new-row grid leaves absent %s blank",
    (outputType) => {
      const { container } = render(<CustomCellRender {...gridProps(outputType)} />);
      expectBlank(container);
      expect(container.querySelector(".MuiSkeleton-root")).toBeNull();
      expect(screen.queryByText("Error")).not.toBeInTheDocument();
    },
  );

  it.each([
    [0, "0%"],
    ["0", "0%"],
    [0.375, "38%"],
    ["0.375", "38%"],
    [1, "100%"],
  ])("P1 preserves score %s as %s", (value, expected) => {
    vi.mocked(normalizeEvalResult).mockReturnValue({
      kind: "score",
      score: Number(value),
    });
    const { container } = render(<EvaluateCell {...evaluationProps(value)} />);
    expect(container.textContent).toBe(expected);
    expect(interpolateColorBasedOnScore).toHaveBeenCalledWith(Number(value), 1);
  });

  it.each([
    [0, "0"],
    ["0", "0"],
    [12, "12"],
    ["12", "12"],
    [0.125, "0.1250"],
    ["1.23456", "1.2346"],
    [-2.5, "-2.5000"],
    ["not a number", "-"],
  ])("P2 preserves numeric formatting for %s as %s", (value, expected) => {
    const { container } = render(<NumericCell value={value} />);
    expect(container.textContent).toBe(expected);
  });

  it("P3 keeps normalized object scores", () => {
    vi.mocked(normalizeEvalResult).mockReturnValue({ kind: "score", score: 0.5 });
    const { container } = render(
      <EvaluateCell {...evaluationProps({ score: 0.5 })} />,
    );
    expect(container.textContent).toBe("50%");
  });

  it("P4 keeps choice labels instead of treating them as an absent score", () => {
    vi.mocked(normalizeEvalResult).mockReturnValue({
      kind: "choices",
      items: ["Relevant"],
      score: 0,
    });
    render(<EvaluateCell {...evaluationProps({ choice: "Relevant", score: 0 })} />);
    expect(screen.getByText("Relevant")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it.each([0, "0"])("P5 keeps explicit metadata result %s", (result) => {
    render(
      <EvaluateCell
        {...evaluationProps(null)}
        cellData={{ value_infos: { data: { result } } }}
      />,
    );
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it.each(["score", "numeric"])(
    "P6 completed-column grid preserves a genuine zero %s result",
    (outputType) => {
      vi.mocked(normalizeEvalResult).mockReturnValue({ kind: "score", score: 0 });
      const { container } = render(
        <CustomCellRender {...gridProps(outputType, "pass", 0)} />,
      );
      expect(container.textContent).toBe(outputType === "score" ? "0%" : "0");
    },
  );

  it.each(["score", "numeric"])(
    "P7 running %s cells retain their skeleton instead of a score",
    (outputType) => {
      const { container } = render(
        <CustomCellRender {...gridProps(outputType, "running")} />,
      );
      expect(container.querySelector(".MuiSkeleton-root")).not.toBeNull();
      expectBlank(container);
      expect(normalizeEvalResult).not.toHaveBeenCalled();
    },
  );

  it.each(["score", "numeric"])(
    "P8 error %s cells retain their error state instead of a score",
    (outputType) => {
      render(<CustomCellRender {...gridProps(outputType, "error")} />);
      expect(screen.getByText("Error")).toBeInTheDocument();
      expect(screen.queryByText(/^0%?$/)).not.toBeInTheDocument();
      expect(normalizeEvalResult).not.toHaveBeenCalled();
      expect(interpolateColorBasedOnScore).not.toHaveBeenCalled();
    },
  );
});
