import React, { useState } from "react";
import PropTypes from "prop-types";
import { render, screen, userEvent, within } from "src/utils/test-utils.jsx";
import { describe, expect, it, vi } from "vitest";

const { Null, Passthrough } = vi.hoisted(() => ({
  Null: () => null,
  Passthrough: ({ children }) => children,
}));

// The real DatapointCard renders each dataset's cards; only the eval grid,
// icons and the modals around the content are stubbed.
vi.mock("ag-grid-react", () => ({ AgGridReact: Null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => undefined }));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/components/svg-color", () => ({ default: Null }));
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

import CompareDatasetDetailContent from "../CompareDatasetDetailDrawer/CompareDatasetDetailContent";

// CompareDatasetsView column_config: dataset A is the base, B is compared.
const outputGroup = {
  id: "output-a",
  name: "output",
  data_type: "text",
  origin: "Dataset",
};
const columnConfig = [
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
// GetCompareDatasetRow row: only the compared cell carries get_diff output.
const row = {
  row_id: "joined-0",
  "output-a": {
    cell_value: "base answer kept",
    cell_diff_value: null,
    cell_row_id: "a-0",
  },
  "output-b": {
    cell_value: "compared answer kept",
    cell_diff_value: [
      { text: "base", status: "removed" },
      { text: "compared", status: "added" },
      { text: "answer", status: "default" },
      { text: "kept", status: "default" },
    ],
    cell_row_id: "b-0",
  },
};

// CompareDatasetDetailDrawer owns showDiff and toggles it the same way.
function DrawerState({ row: shownRow }) {
  const [showDiff, setShowDiff] = useState(false);
  return (
    <CompareDatasetDetailContent
      onClose={vi.fn()}
      row={shownRow}
      columnConfig={columnConfig}
      showDiff={showDiff}
      setShowDiff={setShowDiff}
      handleToggleDiff={() => setShowDiff((prev) => !prev)}
      nextRowId={null}
      prevRowId={null}
      handleFetchNextRow={vi.fn()}
      handleFetchPrevRow={vi.fn()}
      isPending={false}
    />
  );
}

DrawerState.propTypes = { row: PropTypes.object };

const datasetColumns = () =>
  screen.getAllByText("Dataset Details").map((title) => title.closest("td"));

const expectWordDiff = (column) => {
  const removed = within(column).getByText("base", { exact: true });
  const added = within(column).getByText("compared", { exact: true });
  const diff = removed.closest("p");
  expect(added.closest("p")).toBe(diff);
  expect(diff.textContent.replace(/\s+/g, " ").trim()).toBe(
    "base compared answer kept",
  );
};

describe("Compare row detail Difference tab", () => {
  it("renders the compared cell's word diff, and no tab for the base", async () => {
    const user = userEvent.setup();
    render(<DrawerState row={row} />);
    const [base, compared] = datasetColumns();

    expect(within(base).queryByRole("tab", { name: "Difference" })).toBeNull();
    await user.click(within(compared).getByRole("tab", { name: "Difference" }));

    expect(
      within(compared).getByRole("tab", { name: "Difference" }),
    ).toHaveAttribute("aria-selected", "true");
    expectWordDiff(compared);
  });

  it("opens the word diff from the header Show Diff switch", async () => {
    const user = userEvent.setup();
    render(<DrawerState row={row} />);
    const [, compared] = datasetColumns();

    await user.click(screen.getByRole("checkbox"));

    expect(
      within(compared).getByRole("tab", { name: "Difference" }),
    ).toHaveAttribute("aria-selected", "true");
    expectWordDiff(compared);
  });

  it("falls back to Raw when the next row has no diff for that column", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<DrawerState row={row} />);
    await user.click(screen.getByRole("checkbox"));
    expectWordDiff(datasetColumns()[1]);

    // Next row without a cell for the compared output.
    rerender(
      <DrawerState row={{ row_id: "joined-1", "output-a": row["output-a"] }} />,
    );

    const [, compared] = datasetColumns();
    expect(
      within(compared).queryByRole("tab", { name: "Difference" }),
    ).toBeNull();
    expect(within(compared).getByRole("tab", { name: "Raw" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
