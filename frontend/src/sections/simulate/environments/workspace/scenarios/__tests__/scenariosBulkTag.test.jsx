import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "src/utils/test-utils";
import ScenariosStep from "../ScenariosStep";
import { SCENARIO_TAGS_COPY as C } from "../scenarios.constants";
import {
  TEST_ENV,
  envStateFor,
  makeServerRows,
  renderWithClient,
} from "./scenariosTestUtils";

configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      get: vi.fn(async () => ({ data: { tags: [] } })),
      post: vi.fn(async () => ({ data: { changed: 2 } })),
      put: vi.fn(async () => ({ data: {} })),
    },
  };
});

vi.mock("src/api/simulate-environments/scenarios", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/scenarios",
  );
  return {
    ...actual,
    listScenarios: vi.fn(),
    amendScenarios: vi.fn(),
    scenarioCoverage: vi.fn(async () => ({
      axes: [],
      per_axis: [],
      rows: [],
      columns: [],
      cells: [],
    })),
  };
});

const axios = (await import("src/utils/axios")).default;
const { listScenarios } = await import(
  "src/api/simulate-environments/scenarios"
);
const { queryScenarioFixture, resetScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);

const ROWS = makeServerRows(3);

const tagPosts = () =>
  axios.post.mock.calls.filter(([url]) =>
    String(url).includes("scenario-tags/change"),
  );

beforeEach(() => {
  vi.clearAllMocks();
  resetScenarioFixture();
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, ROWS),
  );
});

describe("ScenariosStep bulk tag", () => {
  it("sends the saved scenario keys of the selected rows and clears the selection", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <ScenariosStep
        env={TEST_ENV}
        envState={envStateFor(ROWS)}
        patch={vi.fn()}
        canRun
        onStartRun={vi.fn()}
      />,
    );
    await screen.findByText(ROWS[0].name);
    const boxes = screen.getAllByRole("checkbox");
    const picked = [boxes[1], boxes[2]].map(
      (box) =>
        ROWS.find((row) => box.closest("tr").textContent.includes(row.name))
          .scenario_key,
    );
    await user.click(boxes[1]);
    await user.click(boxes[2]);

    await user.click(screen.getByRole("button", { name: C.tagTip }));
    await user.type(screen.getByPlaceholderText(C.newTag), "urgent{enter}");

    await waitFor(() => expect(tagPosts()).toHaveLength(1));
    const body = tagPosts()[0][1];
    expect([...body.scenario_keys].sort()).toEqual([...picked].sort());
    expect(body.scenario_keys.every((key) => key.startsWith("key-"))).toBe(
      true,
    );
    expect(body).toMatchObject({ add: ["urgent"], remove: [] });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: C.tagTip })).toBeNull(),
    );
    expect(screen.queryByPlaceholderText(C.newTag)).toBeNull();
  });
});
