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
const getHarnessRunningValidation = vi.fn(async () => ({
  job_id: null,
  scenario_keys: null,
  to_prove: 4,
  message: null,
}));
const enqueueSnackbar = vi.fn();
const listHarnessScenarios = vi.fn(async () => ({ results: [] }));
vi.mock("src/api/harness/harness", async () => ({
  ...(await vi.importActual("src/api/harness/harness")),
  listHarnessScenarios: (...args) => listHarnessScenarios(...args),
  validateHarnessScenarios: (...args) => validateHarnessScenarios(...args),
  getHarnessScenarioValidation: (...args) =>
    getHarnessScenarioValidation(...args),
  getHarnessRunningValidation: (...args) =>
    getHarnessRunningValidation(...args),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const { default: ValidateScenariosButton } = await import(
  "../ValidateScenariosButton"
);
const { scenariosListKey } = await import("src/api/harness/scenarios");

// The button waits for the page's running validation to be read before it can be pressed.
const enabledButton = async (name) => {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
};

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

    await user.click(await enabledButton("Validate cases not yet proved"));
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

    await user.click(await enabledButton("Validate scenario against the environment"));

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

    await user.click(await enabledButton("Validate cases not yet proved"));

    expect(
      await screen.findByRole("button", { name: "Validating · proved 120 of 500" }),
    ).toBeDisabled();
  });
});

const renderButton = (props) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ValidateScenariosButton {...props} />
    </QueryClientProvider>,
  );
};

describe("A validation started before the page opened", () => {
  it("is followed by the case's Validate, which starts no second one", async () => {
    validateHarnessScenarios.mockClear();
    enqueueSnackbar.mockClear();
    getHarnessRunningValidation.mockResolvedValueOnce({
      job_id: "val-9",
      scenario_keys: ["refund-o10"],
      to_prove: 3,
      message: null,
    });
    getHarnessScenarioValidation.mockReset();
    getHarnessScenarioValidation
      .mockResolvedValueOnce({ state: "validating", proofs_recorded: false, proved: 0, total: 1 })
      .mockResolvedValue({ state: "finished", proofs_recorded: true, proved: 1, total: 1 });

    renderButton({
      jobId: "env-3",
      keys: ["refund-o10"],
      label: "Validate scenario against the environment",
    });

    expect(
      await screen.findByRole("button", { name: "Validating · proved 0 of 1" }),
    ).toBeDisabled();
    expect(getHarnessScenarioValidation).toHaveBeenCalledWith("env-3", "val-9");
    expect(validateHarnessScenarios).not.toHaveBeenCalled();
  });

  it("disables a case's Validate while a validation of other cases runs", async () => {
    getHarnessRunningValidation.mockResolvedValueOnce({
      job_id: "val-10",
      scenario_keys: ["refund-o11"],
      to_prove: 3,
      message: null,
    });

    renderButton({
      jobId: "env-4",
      keys: ["refund-o10"],
      label: "Validate scenario against the environment",
    });

    expect(
      await screen.findByText(
        "Another validation is running; validate this case when it ends.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Validate scenario against the environment" }),
    ).toBeDisabled();
  });

  const finishedRun = (jobId, counts) => {
    enqueueSnackbar.mockClear();
    getHarnessRunningValidation.mockResolvedValueOnce({
      job_id: jobId,
      scenario_keys: null,
      to_prove: 3,
      message: null,
    });
    getHarnessScenarioValidation.mockReset();
    // The job's own count reaches every case it touched, kept as written ones included.
    getHarnessScenarioValidation.mockResolvedValue({
      state: "finished",
      proofs_recorded: true,
      proved: 61,
      total: 61,
    });
    listHarnessScenarios.mockResolvedValue({
      results: [],
      lifecycle_counts: counts,
    });
  };

  it("says once, as a toast, what the validation it followed left, as the counts line does", async () => {
    finishedRun("val-11", { proved: 45, kept_as_written: 16, not_proved: 0 });

    renderButton({ jobId: "env-5" });

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Validation finished · 45 proved, 16 kept as written",
        { variant: "success" },
      ),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });

  it("names the cases left not proved", async () => {
    finishedRun("val-12", { proved: 58, kept_as_written: 0, not_proved: 3 });

    renderButton({ jobId: "env-6" });

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Validation finished · 58 proved, 3 not proved",
        { variant: "success" },
      ),
    );
  });

  it("does not raise the toast again when the button mounts again", async () => {
    finishedRun("val-13", { proved: 4, kept_as_written: 0, not_proved: 0 });
    const first = renderButton({ jobId: "env-7" });
    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalledTimes(1));
    first.unmount();

    getHarnessRunningValidation.mockResolvedValueOnce({
      job_id: "val-13",
      scenario_keys: null,
      to_prove: 0,
      message: null,
    });
    renderButton({ jobId: "env-7" });
    await waitFor(() =>
      expect(getHarnessScenarioValidation.mock.calls.length).toBeGreaterThan(1),
    );
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });
});

describe("Validate cases not yet proved, with none to prove", () => {
  it("says so before the press, and why", async () => {
    const why =
      "Nothing to validate: the 4 cases not proved are held for a reason validating again would not change.";
    getHarnessRunningValidation.mockResolvedValueOnce({
      job_id: null,
      scenario_keys: null,
      to_prove: 0,
      message: why,
    });

    renderButton({ jobId: "env-6" });

    const button = await screen.findByRole("button", { name: "Nothing to validate" });
    expect(button).toBeDisabled();
    await userEvent.setup().hover(button.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(why);
  });

  it("shows the server's reason when a press finds nothing to prove", async () => {
    const user = userEvent.setup();
    const why = "Nothing to validate: the case not proved is held for a reason validating again would not change.";
    validateHarnessScenarios.mockResolvedValueOnce({ state: "ready", message: why });

    renderButton({ jobId: "env-7" });
    await user.click(
      await screen.findByRole("button", { name: "Validate cases not yet proved" }),
    );

    expect(await screen.findByRole("status")).toHaveTextContent(why);
  });
});
