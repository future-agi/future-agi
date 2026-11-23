import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";

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
  it("asks to prove every scenario again for Re-prove all", async () => {
    renderButton({ reproveAll: true, label: "Re-prove all" });
    await pressWhenReady("Re-prove all");
    await waitFor(() =>
      expect(validateScenarios).toHaveBeenCalledWith("env-1", {
        reproveAll: true,
      }),
    );
  });

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
      await screen.findByRole("button", { name: "Validating · proved 2 of 5" }),
    ).toBeDisabled();
    expect(getScenarioValidation).toHaveBeenCalledWith("env-1", "val-9");
    expect(validateScenarios).not.toHaveBeenCalled();
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
});
