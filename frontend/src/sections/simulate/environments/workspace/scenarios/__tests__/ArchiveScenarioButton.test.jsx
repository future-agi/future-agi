import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";

const archiveScenario = vi.fn();
const restoreScenario = vi.fn();
vi.mock("src/api/simulate-environments/scenarios", async () => ({
  ...(await vi.importActual("src/api/simulate-environments/scenarios")),
  archiveScenario: (...args) => archiveScenario(...args),
  restoreScenario: (...args) => restoreScenario(...args),
}));
const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const { default: ArchiveScenarioButton } = await import(
  "../ArchiveScenarioButton"
);

const row = (state) => ({ scenarioKey: "refund-o10", lifecycle: { state } });

const renderButton = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ArchiveScenarioButton envId="env-1" {...props} />
    </QueryClientProvider>,
  );

beforeEach(() => vi.clearAllMocks());

describe("ArchiveScenarioButton", () => {
  it("says to remove a scenario only the unsaved changes hold, and leaves its state alone", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    // The request layer rejects with the server's error body itself.
    archiveScenario.mockRejectedValue({
      error: "scenario_unsaved",
      message: "x",
      statusCode: 409,
    });
    renderButton({ row: row("active"), onChanged });

    await user.click(screen.getByRole("button", { name: "Archive" }));
    await user.click(
      await screen
        .findAllByRole("button", { name: "Archive" })
        .then((all) => all.at(-1)),
    );

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "This scenario only exists in the unsaved changes. Remove it instead.",
        { variant: "error" },
      ),
    );
    expect(onChanged).not.toHaveBeenCalled();
  });

  it.each([
    ["active", "Restored. It runs again.", "success"],
    ["draft", "Restored, but held back. Its Status says why.", "warning"],
  ])(
    "says whether a restored %s scenario runs",
    async (state, said, variant) => {
      const user = userEvent.setup();
      restoreScenario.mockResolvedValue({ live: true, lifecycle: { state } });
      renderButton({ row: row("archived") });

      await user.click(screen.getByRole("button", { name: "Restore" }));

      await waitFor(() =>
        expect(enqueueSnackbar).toHaveBeenCalledWith(said, { variant }),
      );
    },
  );

  it("says a restore that lands in the unsaved changes needs a save", async () => {
    const user = userEvent.setup();
    restoreScenario.mockResolvedValue({
      live: false,
      lifecycle: { state: "draft" },
    });
    renderButton({ row: row("archived") });

    await user.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Restored into the unsaved changes. Save to run it.",
        { variant: "success" },
      ),
    );
  });
});
