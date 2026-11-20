import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/api/harness/scenarios", () => ({
  useArchiveScenario: () => ({
    mutateAsync: mocks.mutateAsync,
    isPending: false,
  }),
  useScenarioRevisions: () => ({ data: { revisions: [] } }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
// The sheet's own reads are not what these tests are about.
vi.mock("../StartingRecords", () => ({ default: () => null }));
vi.mock("../ScenarioDetail", () => ({ LifecycleSections: () => null }));
import { scenarioFromApi } from "src/api/simulate-environments/scenarios";
import ScenarioDrawer from "../ScenarioDrawer";
import { SCENARIOS_COPY } from "../scenarios.constants";

const ARCHIVED = {
  id: "s1",
  scenario_key: "refund_twice",
  number: 3,
  name: "refund_twice",
  lifecycle: { state: "archived", reason: "archived" },
};

describe("Restoring an archived scenario", () => {
  beforeEach(() => {
    mocks.mutateAsync.mockReset();
    mocks.enqueueSnackbar.mockReset();
  });

  it.each([
    ["active", SCENARIOS_COPY.restoredLive],
    ["kept_as_written", SCENARIOS_COPY.restoredLive],
    ["draft", SCENARIOS_COPY.restored],
  ])("says what the case comes back as: %s", async (state, copy) => {
    // A case still in the saved suite can come back Draft: `live` alone does not say it runs.
    mocks.mutateAsync.mockResolvedValue({
      scenario_key: "refund_twice",
      live: true,
      lifecycle: { state },
    });
    render(
      <ScenarioDrawer
        row={scenarioFromApi(ARCHIVED)}
        env={{ id: "env-1" }}
        onClose={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: SCENARIOS_COPY.restore }),
    );
    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(copy, {
        variant: "success",
      }),
    );
  });
});
