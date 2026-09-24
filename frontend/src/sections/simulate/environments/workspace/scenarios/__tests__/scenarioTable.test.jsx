import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import ScenarioTable from "../ScenarioTable";
import { SCENARIOS_COPY } from "../scenarios.constants";

const env = MOCK_WORLD;

const makeRows = () => [
  { id: "s1", name: "first", useCase: "Refunds", situation: "A", expected: "B" },
  { id: "s2", name: "second", useCase: "Refunds", situation: "A", expected: "B" },
];

const renderTable = (rows) =>
  render(
    <ScenarioTable rows={rows} env={env} onEdit={vi.fn()} onRemove={vi.fn()} />,
  );

const rowCheckboxes = () =>
  screen
    .getAllByRole("checkbox")
    .filter((box) => box.getAttribute("aria-label") !== SCENARIOS_COPY.selectAll);

describe("ScenarioTable selection", () => {
  it("keeps a row unchecked when the rows array is rebuilt with the same ids", async () => {
    const user = userEvent.setup();
    const { rerender } = renderTable(makeRows());

    const [first] = rowCheckboxes();
    expect(first).toBeChecked();
    await user.click(first);
    expect(rowCheckboxes()[0]).not.toBeChecked();

    // A poll hands the table a fresh array of the same rows. `allIds` changes
    // identity, which used to re-run the sync effect and re-check the row the
    // user had just unchecked.
    rerender(
      <ScenarioTable rows={makeRows()} env={env} onEdit={vi.fn()} onRemove={vi.fn()} />,
    );

    expect(rowCheckboxes()[0]).not.toBeChecked();
  });

  it("selects a row that is genuinely new", () => {
    const { rerender } = renderTable(makeRows());
    const added = [...makeRows(), { id: "s3", name: "third", useCase: "Refunds" }];

    rerender(
      <ScenarioTable rows={added} env={env} onEdit={vi.fn()} onRemove={vi.fn()} />,
    );

    rowCheckboxes().forEach((box) => expect(box).toBeChecked());
  });

  it("labels the select-all checkbox", () => {
    renderTable(makeRows());
    expect(
      screen.getByRole("checkbox", { name: SCENARIOS_COPY.selectAll }),
    ).toBeInTheDocument();
  });
});
