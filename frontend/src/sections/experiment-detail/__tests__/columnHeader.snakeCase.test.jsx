// Regression test for the experiment snake_case hotfix: Data grid column header.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  EDT_A_ID,
  OUTPUT_A_COLUMN_ID,
  llmColumnConfig,
} from "./experiment-snake-case.fixtures";

vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="type-icon" data-icon={icon} />,
}));

import CustomExperimentColumnHeader from "../ExperimentData/CustomExperimentColumnHeader";
import { useRerunColumnInExperimentStoreShallow } from "../ExperimentData/states";

const SelectedSourceProbe = () => {
  const selected = useRerunColumnInExperimentStoreShallow(
    (state) => state.selectedSourceId,
  );
  return <output aria-label="rerun source">{selected ?? ""}</output>;
};

describe("Experiment column header reads the rows column_config's snake_case keys", () => {
  it("D7: renders 'Re run' for an experiment output column and passes its source_id; shows the type icon for a dataset column", () => {
    // views/experiments.py:1450-1468 sends origin_type / data_type / source_id
    const outputCol = llmColumnConfig.find((c) => c.id === OUTPUT_A_COLUMN_ID);

    const { unmount } = render(
      <>
        <CustomExperimentColumnHeader
          displayName={outputCol.name}
          col={outputCol}
          hideMenu
          isGrouped
          head="A"
          index={1}
        />
        <SelectedSourceProbe />
      </>,
    );
    // The button is display:none until the header is hovered, so it is found
    // by its text rather than by accessible role.
    fireEvent.click(screen.getByText("Re run").closest("button"));
    expect(screen.getByLabelText("rerun source")).toHaveTextContent(EDT_A_ID);
    unmount();

    // views/experiments.py:1187-1206: a dataset column, origin_type "OTHERS",
    // data_type "text" -> the text type icon.
    const baseCol = llmColumnConfig[0];
    render(
      <CustomExperimentColumnHeader
        displayName={baseCol.name}
        col={baseCol}
        hideMenu
      />,
    );
    expect(screen.getByTestId("type-icon")).toHaveAttribute(
      "data-icon",
      "material-symbols:notes",
    );
    expect(screen.queryByText("Re run")).toBeNull();
  });
});
