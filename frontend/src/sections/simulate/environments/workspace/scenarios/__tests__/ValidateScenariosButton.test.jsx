import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "src/utils/test-utils";

const validateScenarios = vi.fn();
const getScenarioValidation = vi.fn();
const getRunningScenarioValidation = vi.fn();
vi.mock("src/api/simulate-environments/scenarios", async () => ({
  ...(await vi.importActual("src/api/simulate-environments/scenarios")),
  validateScenarios: (...args) => validateScenarios(...args),
  getScenarioValidation: (...args) => getScenarioValidation(...args),
  getRunningScenarioValidation: (...args) =>
    getRunningScenarioValidation(...args),
}));
const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const { default: ValidateScenariosButton } = await import(
  "../ValidateScenariosButton"
);

const NOTHING_RUNNING = {
  job_id: null,
  scenario_keys: null,
  to_prove: 4,
  message: null,
};

const renderButton = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ValidateScenariosButton envId="env-1" {...props} />
    </QueryClientProvider>,
  );

const pressWhenReady = async (name) => {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.setup().click(button);
};

beforeEach(() => {
  vi.clearAllMocks();
  getRunningScenarioValidation.mockResolvedValue(NOTHING_RUNNING);
  getScenarioValidation.mockResolvedValue({
    state: "finished",
    proofs_recorded: true,
  });
  validateScenarios.mockResolvedValue({ state: "validating", job_id: "val-1" });
});

describe("ValidateScenariosButton", () => {
  it("confirms with the count before proving every scenario again", async () => {
    renderButton({ reproveAll: true, label: "Re-prove all", confirmCount: 54 });
    await pressWhenReady("Re-prove all");
    expect(
      await screen.findByText("Re-prove all 54 scenarios?"),
    ).toBeInTheDocument();
    expect(validateScenarios).not.toHaveBeenCalled();
    await userEvent.setup().click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Re-prove all",
      }),
    );
    await waitFor(() =>
      expect(validateScenarios).toHaveBeenCalledWith("env-1", {
        reproveAll: true,
      }),
    );
  });

  // Proofs land only when the validation ends, so it says how many it is proving, not how many are done.
  it("follows a validation already running instead of starting another", async () => {
    getRunningScenarioValidation.mockResolvedValue({
      ...NOTHING_RUNNING,
      job_id: "val-9",
    });
    getScenarioValidation.mockResolvedValue({
      state: "validating",
      proved: 2,
      total: 5,
    });
    renderButton();
    expect(
      await screen.findByRole("button", { name: "Validating 3 scenarios" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Save waits until it finishes."),
    ).toBeInTheDocument();
    expect(getScenarioValidation).toHaveBeenCalledWith("env-1", "val-9");
    expect(validateScenarios).not.toHaveBeenCalled();
  });

  it("follows a second validation that starts after the first one ended", async () => {
    getRunningScenarioValidation
      .mockResolvedValueOnce({ ...NOTHING_RUNNING, job_id: "val-9" })
      .mockResolvedValue({ ...NOTHING_RUNNING, job_id: "val-10" });
    getScenarioValidation.mockResolvedValue({
      state: "finished",
      proofs_recorded: true,
      proved: 1,
      total: 1,
    });
    renderButton();
    await waitFor(() =>
      expect(getScenarioValidation).toHaveBeenCalledWith("env-1", "val-10"),
    );
    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(2));
  });

  it("says why when there is nothing to prove, without a progress state", async () => {
    validateScenarios.mockResolvedValue({
      state: "ready",
      message: "Every case is proved.",
    });
    renderButton();
    await pressWhenReady("Validate cases not yet proved");
    expect(
      await screen.findByText("Every case is proved."),
    ).toBeInTheDocument();
    expect(getScenarioValidation).not.toHaveBeenCalled();
  });

  it("says a finished validation recorded no results", async () => {
    getScenarioValidation.mockResolvedValue({
      state: "finished",
      proofs_recorded: false,
      message: null,
    });
    renderButton();
    await pressWhenReady("Validate cases not yet proved");
    expect(
      await screen.findByText(
        "Validation finished but recorded no results. Try again.",
      ),
    ).toBeInTheDocument();
  });

  it("says a validation that didn't finish changed nothing, and offers Try again", async () => {
    getScenarioValidation.mockResolvedValue({
      state: "failed",
      message: "The sandbox ran out of time.",
    });
    renderButton();
    await pressWhenReady("Validate cases not yet proved");
    expect(
      await screen.findByText(
        "Validate didn't finish: The sandbox ran out of time. Nothing changed; scenarios keep their state.",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Try again" }),
    ).toBeEnabled();
  });
});
