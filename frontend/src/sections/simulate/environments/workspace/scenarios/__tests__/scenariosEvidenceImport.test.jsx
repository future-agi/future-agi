import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { configure } from "@testing-library/react";
import { screen, fireEvent, waitFor } from "src/utils/test-utils";

import ScenariosStep from "../ScenariosStep";
import {
  TEST_ENV,
  SERVER_ROWS,
  envStateFor,
  renderWithClient,
} from "./scenariosTestUtils";

// An import opened from Observe lands on the Scenarios tab as `?import=`: the
// tab opens its review, and closing the review drops only that param.

configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));

const drawer = vi.hoisted(() => ({ props: null }));
vi.mock(
  "src/sections/simulate/environments/evidence/EvidenceReviewDrawer",
  () => ({
    default: (props) => {
      drawer.props = props;
      return (
        // Through the captured props, which the assertions read too.
        <button type="button" onClick={() => drawer.props.onClose()}>
          close review
        </button>
      );
    },
  }),
);
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }),
}));
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
const { listScenarios } = await import(
  "src/api/simulate-environments/scenarios"
);
const { queryScenarioFixture, resetScenarioFixture } = await import(
  "src/api/simulate-environments/_fixtures/scenariosFixtures"
);

const IMPORT = "22222222-2222-2222-2222-222222222222";

const renderAt = (search) => {
  window.history.pushState({}, "", `/environments/${TEST_ENV.id}${search}`);
  renderWithClient(
    <ScenariosStep env={TEST_ENV} envState={envStateFor()} patch={vi.fn()} />,
  );
};

beforeEach(() => {
  drawer.props = null;
  resetScenarioFixture();
  listScenarios.mockReset();
  listScenarios.mockImplementation((jobId, params) =>
    queryScenarioFixture(params, SERVER_ROWS),
  );
});

describe("ScenariosStep evidence import", () => {
  it("opens the review named in the URL and closes it without losing the tab", async () => {
    renderAt(`?tab=scenarios&import=${IMPORT}`);
    await waitFor(() =>
      expect(drawer.props).toMatchObject({
        envId: TEST_ENV.id,
        importId: IMPORT,
        open: true,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "close review" }));

    await waitFor(() => expect(window.location.search).toBe("?tab=scenarios"));
    expect(screen.queryByRole("button", { name: "close review" })).toBeNull();
  });

  it("opens no review without an import, and offers the way in", async () => {
    renderAt("?tab=scenarios");
    expect(
      await screen.findByRole("button", { name: "Import evidence" }),
    ).toBeInTheDocument();
    expect(drawer.props).toBeNull();
  });
});
