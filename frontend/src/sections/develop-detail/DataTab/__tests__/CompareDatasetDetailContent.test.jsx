import React from "react";
import { render, screen, within } from "src/utils/test-utils.jsx";
import { describe, expect, it, vi } from "vitest";

const { Null, Passthrough, cards } = vi.hoisted(() => ({
  Null: () => null,
  Passthrough: ({ children }) => children,
  cards: [],
}));

vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon" data-icon={icon} />,
}));
vi.mock("src/components/svg-color", () => ({
  default: ({ src }) => <span data-testid="icon" data-icon={src} />,
}));
vi.mock("src/components/tooltip", () => ({ default: Passthrough }));
vi.mock("src/components/custom-audio/AudioDatapointCard", () => ({
  default: Null,
}));
vi.mock("src/sections/common/ImagesDatapointCard", () => ({ default: Null }));
vi.mock(
  "src/sections/experiment-detail/ExperimentData/ExperimentDescriptionCell",
  () => ({ default: Null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentData/ViewEvalDetailsModal",
  () => ({ default: Null }),
);
vi.mock(
  "../CompareDatasetDetailDrawer/CompareDatasetEvalsAddFeedbackForm",
  () => ({ default: Null }),
);
vi.mock("src/sections/common/DatapointCard", () => ({
  default: (props) => {
    cards.push(props);
    return <div data-testid="datapoint">{props.value?.cell_value}</div>;
  },
}));

import CompareDatasetDetailContent from "../CompareDatasetDetailDrawer/CompareDatasetDetailContent";
import CustomCompareColumnHeader from "../CustomCompareColumnHeader";

// CompareDatasetsView column_config and GetCompareDatasetRow row (snake_case).
const outputGroup = {
  id: "output-a",
  name: "output",
  data_type: "text",
  origin: "Dataset",
};
const columnConfig = [
  {
    id: "input-a",
    name: "input",
    data_type: "text",
    origin_type: "others",
    dataset_id: "dataset-a",
    group: {
      id: "input-a",
      name: "input",
      data_type: "text",
      origin: "Dataset",
    },
  },
  {
    id: "output-a",
    name: "Dataset A",
    data_type: "text",
    origin_type: "others",
    dataset_id: "dataset-a",
    group: outputGroup,
  },
  {
    id: "output-b",
    name: "Dataset B",
    data_type: "text",
    origin_type: "others",
    dataset_id: "dataset-b",
    group: outputGroup,
  },
];
const row = {
  row_id: "joined-0",
  "input-a": { cell_value: "input-0", cell_row_id: "a-0" },
  "output-a": { cell_value: "left-0", cell_row_id: "a-0" },
  "output-b": {
    cell_value: "right-0",
    // model_hub.utils.utils.get_diff of the base output against this one.
    cell_diff_value: [
      { text: "left-0", status: "removed" },
      { text: "right-0", status: "added" },
    ],
    cell_row_id: "b-0",
  },
};

describe("Compare row detail content", () => {
  it("shows one column per compared dataset with that dataset's values", () => {
    cards.length = 0;
    render(
      <CompareDatasetDetailContent
        onClose={vi.fn()}
        row={row}
        columnConfig={columnConfig}
        showDiff={false}
        setShowDiff={vi.fn()}
        handleToggleDiff={vi.fn()}
        nextRowId={null}
        prevRowId={null}
        handleFetchNextRow={vi.fn()}
        handleFetchPrevRow={vi.fn()}
        isPending={false}
      />,
    );

    const headers = screen
      .getAllByRole("columnheader")
      .map((header) => header.textContent.replace(/\s+/g, " ").trim());
    expect(headers).toEqual(["A Dataset A", "B Dataset B"]);
    const [left, right] = screen.getAllByRole("row")[2].querySelectorAll("td");
    expect(
      within(left)
        .getAllByTestId("datapoint")
        .map((card) => card.textContent),
    ).toEqual(["left-0"]);
    expect(
      within(right)
        .getAllByTestId("datapoint")
        .map((card) => card.textContent),
    ).toEqual(["right-0"]);
    const rightCard = cards.find(
      (card) => card.value?.cell_value === row["output-b"].cell_value,
    );
    expect(rightCard.column).toEqual({
      dataType: "text",
      headerName: "output",
    });
    expect(rightCard.showDiff).toBe(row["output-b"].cell_diff_value);
    // DatapointCard's Difference tab renders value.cellDiffValue.
    expect(rightCard.value.cellDiffValue).toBe(row["output-b"].cell_diff_value);
  });
});

describe("Compare column header", () => {
  it("shows the type icon from the snake_case column config", () => {
    render(
      <CustomCompareColumnHeader
        displayName="input"
        col={columnConfig[0]}
        hideMenu
      />,
    );
    expect(screen.getByTestId("icon")).toHaveAttribute(
      "data-icon",
      "solar:notes-linear",
    );
  });
});
