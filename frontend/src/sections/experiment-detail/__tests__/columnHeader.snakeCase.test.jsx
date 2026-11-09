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

import CustomExperimentColumnHeader from "../../experiment-detail-classic/ExperimentData/CustomExperimentColumnHeader";
import { useRerunColumnInExperimentStoreShallow } from "../../experiment-detail-classic/ExperimentData/states";

const SelectedSourceProbe = () => {
  const selected = useRerunColumnInExperimentStoreShallow(
    (state) => state.selectedSourceId,
  );
  return <output aria-label="rerun source">{selected ?? ""}</output>;
};

describe("Experiment column header reads the rows column_config's snake_case keys", () => {
  it("D7: keeps column Re run hidden for an experiment output and never selects its source_id; shows the dataset type icon", () => {
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
    // L3's accepted launch contract hides this action even on hover; it must
    // not expose an inert button or change the selected source behind it.
    expect(outputCol.source_id).toBe(EDT_A_ID);
    fireEvent.mouseOver(screen.getByText(outputCol.name));
    expect(screen.queryByText("Re run")).toBeNull();
    expect(screen.queryByRole("button", { name: "Re run", hidden: true })).toBeNull();
    expect(screen.getByLabelText("rerun source")).toHaveTextContent(/^$/);
    expect(screen.getByLabelText("rerun source")).not.toHaveTextContent(EDT_A_ID);
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
