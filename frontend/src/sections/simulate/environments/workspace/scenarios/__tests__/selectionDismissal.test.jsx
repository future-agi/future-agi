import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "src/utils/test-utils";
import { queryScenarioFixture } from "src/api/simulate-environments/_fixtures/scenariosFixtures";
import ChatSplitPane from "../../../components/ChatSplitPane";
import ScenariosStep from "../ScenariosStep";
import {
  envStateFor,
  makeServerRows,
  renderWithClient,
  TEST_ENV,
} from "./scenariosTestUtils";

const rows = makeServerRows(2);
vi.mock("src/api/simulate-environments/scenarios", async (importOriginal) => ({
  ...(await importOriginal()),
  listScenarios: async (_id, params) => queryScenarioFixture(params, rows),
  scenarioCoverage: async () => ({
    axes: [],
    rows: [],
    columns: [],
    cells: [],
  }),
}));
vi.mock("../../../evidence/DraftImports", () => ({ default: () => null }));

async function selectRow() {
  renderWithClient(
    <>
      <h1>Workspace</h1>
      <ChatSplitPane chat={() => <textarea aria-label="Builder message" />}>
        <ScenariosStep
          env={TEST_ENV}
          envState={envStateFor(rows)}
          patch={vi.fn()}
          canRun
          onStartRun={vi.fn()}
        />
      </ChatSplitPane>
    </>,
  );
  const table = await screen.findByRole("table");
  const checkbox = within(table).getAllByRole("checkbox")[1];
  fireEvent.click(checkbox);
  expect(checkbox).toBeChecked();
  return checkbox;
}

describe("unit: scenario selection dismissal", () => {
  it.each([
    ["Escape", (box) => fireEvent.keyDown(box, { key: "Escape" })],
    ["outside click", () => fireEvent.click(screen.getByText("Workspace"))],
    [
      "clear control",
      () =>
        fireEvent.click(
          screen.getByRole("button", { name: "Clear selection" }),
        ),
    ],
  ])(
    "clears the checked row and run selection on %s",
    async (_name, dismiss) => {
      const checkbox = await selectRow();
      dismiss(checkbox);
      expect(checkbox).not.toBeChecked();
      expect(
        screen.queryByRole("button", { name: "Clear selection" }),
      ).toBeNull();
    },
  );

  it("keeps the selection available when composing a builder message", async () => {
    const checkbox = await selectRow();
    fireEvent.click(screen.getByRole("textbox", { name: "Builder message" }));
    expect(checkbox).toBeChecked();
  });
});
