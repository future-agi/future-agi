import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";

const validateHarnessScenarios = vi.fn(async () => ({
  state: "validating",
  job_id: "val-1",
}));
const getHarnessScenarioValidation = vi.fn(async () => ({
  state: "finished",
  proofs_recorded: true,
}));
vi.mock("src/api/harness/harness", async () => ({
  ...(await vi.importActual("src/api/harness/harness")),
  validateHarnessScenarios: (...args) => validateHarnessScenarios(...args),
  getHarnessScenarioValidation: (...args) =>
    getHarnessScenarioValidation(...args),
}));

const { default: ValidateScenariosButton } = await import(
  "../ValidateScenariosButton"
);
const { scenariosListKey } = await import("src/api/harness/scenarios");

describe("Validate scenarios", () => {
  it("does not read the ended validation again when the scenario list is refreshed", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ValidateScenariosButton jobId="env-1" />
      </QueryClientProvider>,
    );

    await user.click(
      screen.getByRole("button", { name: "Validate cases not yet proved" }),
    );
    await waitFor(() =>
      expect(getHarnessScenarioValidation).toHaveBeenCalledTimes(1),
    );
    expect(
      await screen.findByRole("button", { name: "Validate cases not yet proved" }),
    ).toBeEnabled();

    // Its end refreshes the list. Under the list's key, the status was read again, which refreshed
    // the list again: the page polled the server in a loop.
    await client.invalidateQueries({ queryKey: scenariosListKey("env-1") });
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(getHarnessScenarioValidation).toHaveBeenCalledTimes(1);
  });
});

describe("Validate one scenario", () => {
  it("sends only that case's key", async () => {
    const user = userEvent.setup();
    validateHarnessScenarios.mockClear();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ValidateScenariosButton
          jobId="env-1"
          keys={["refund-o10"]}
          label="Validate scenario against the environment"
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Validate scenario against the environment" }));

    await waitFor(() =>
      expect(validateHarnessScenarios).toHaveBeenCalledWith("env-1", { keys: ["refund-o10"] }),
    );
  });
});

describe("A running validation", () => {
  it("says how many cases are proved of how many, from the validation's own counts", async () => {
    const user = userEvent.setup();
    getHarnessScenarioValidation.mockImplementation(async () => ({
      state: "validating",
      proofs_recorded: false,
      proved: 120,
      total: 500,
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ValidateScenariosButton jobId="env-2" />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Validate cases not yet proved" }));

    expect(
      await screen.findByRole("button", { name: "Validating · proved 120 of 500" }),
    ).toBeDisabled();
  });
});
