import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  within,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import {
  useEnvironmentRunTest,
  useRemoveAppliedEvaluation,
} from "src/api/simulate-environments/environments";
import AllEvaluationsDrawer from "../AllEvaluationsDrawer";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/simulate-environments/environments", () => ({
  useEnvironmentRunTest: vi.fn(),
  useRemoveAppliedEvaluation: vi.fn(),
}));
vi.mock("src/api/simulate-environments/runEvals", () => ({
  runResultsKey: (id) => ["simulation-run-results-v3", id],
  runAnalyticsKey: (id) => ["simulation-run-analytics-v3", id],
  callDetailKeyPrefix: ["simulation-call-detail-v3"],
  compareRunsKeyPrefix: ["harness-run-compare"],
}));

const CONFIGS = [
  {
    id: "c1",
    name: "no_misselling",
    mapping: { conversation: "voice_recording" },
    model: "turing_large",
    eval_type: "llm",
    regradable: true,
    editable: true,
  },
  {
    id: "c2",
    name: "tone_check",
    mapping: { input: "transcript" },
    eval_type: "agent",
    regradable: true,
    editable: true,
  },
  {
    id: "c3",
    name: "refund_issued_claim",
    mapping: {},
    eval_type: "llm",
    regradable: false,
    editable: false,
  },
];

// The environment's own `evaluations.selected[]`: the same config ids, with
// the description and the labelled inputs the run test doesn't send.
const APPLIED = [
  {
    id: "c1",
    name: "no_misselling",
    description: "Checks the agent never mis-sells a policy.",
    inputs: [
      {
        key: "conversation",
        source: "voice_recording",
        label: "Call recording",
      },
    ],
  },
];

let removeMutate;
let removeMutateAsync;

beforeEach(() => {
  vi.clearAllMocks();
  useEnvironmentRunTest.mockReturnValue({
    data: CONFIGS,
    isPending: false,
    isError: false,
  });
  removeMutate = vi.fn();
  removeMutateAsync = vi.fn(() => Promise.resolve());
  useRemoveAppliedEvaluation.mockReturnValue({
    mutate: removeMutate,
    mutateAsync: removeMutateAsync,
    isPending: false,
  });
});

const setup = (props = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <AllEvaluationsDrawer
        open
        env={{ id: "env-1" }}
        runTestId="rt1"
        executionId="ex1"
        canRun
        appliedEvals={APPLIED}
        onClose={vi.fn()}
        onRerun={vi.fn()}
        onEdit={vi.fn()}
        onAddEvaluations={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
};

describe("AllEvaluationsDrawer (Vel's applied evals)", () => {
  it("All Evaluations: select all, bulk Remove N asks to confirm, rows expand to blurb and mapping", async () => {
    const { invalidateSpy } = setup();

    expect(screen.queryByRole("button", { name: /^Remove \d/ })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Evals (3)" }));

    // Ticking swaps Add for Clear and a bulk remove of what's ticked.
    expect(
      screen.queryByRole("button", { name: "Add Evaluations" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove 2" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Remove 2 evals?",
    });
    expect(
      within(dialog).getByText(
        "New runs won't be graded on these. Past scores stay.",
      ),
    ).toBeInTheDocument();
    // Nothing goes until it's confirmed.
    expect(removeMutateAsync).not.toHaveBeenCalled();
    expect(removeMutate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Remove 2" }));
    await waitFor(() => expect(removeMutateAsync).toHaveBeenCalledTimes(2));
    expect(removeMutateAsync).toHaveBeenCalledWith({
      id: "env-1",
      evalConfigId: "c1",
    });
    expect(removeMutateAsync).toHaveBeenCalledWith({
      id: "env-1",
      evalConfigId: "c2",
    });
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["simulation-run-results-v3", "ex1"],
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-run-analytics-v3", "ex1"],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-call-detail-v3"],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["harness-run-compare"],
    });

    // The confirm closes once the removes are sent.
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Remove 2 evals?" }),
      ).toBeNull(),
    );

    // A row opens to its description and its mapping, labelled as the
    // environment labels it.
    expect(screen.queryByText(APPLIED[0].description)).toBeNull();
    const expand = screen.getByRole("button", {
      name: "Show details for no_misselling",
    });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(expand);
    expect(expand).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(APPLIED[0].description)).toBeInTheDocument();
    expect(screen.getByText("{{conversation}}")).toBeInTheDocument();
    expect(screen.getByText("Call recording")).toBeInTheDocument();
  });

  it("cancelling the bulk remove removes nothing", async () => {
    setup();

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select tone_check" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove 1" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Remove 1 eval?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(removeMutateAsync).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Remove 1" }),
    ).toBeInTheDocument();
  });

  it("Clear drops the ticks and brings Add back", () => {
    setup();

    fireEvent.click(screen.getByRole("checkbox", { name: "Evals (3)" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));

    expect(screen.queryByRole("button", { name: /^Remove \d/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Add Evaluations" }),
    ).toBeInTheDocument();
  });

  it("holds the bulk remove while the run is being graded", () => {
    setup({ canRun: false, grading: true });

    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select tone_check" }),
    );
    expect(screen.getByRole("button", { name: "Remove 1" })).toBeDisabled();
  });

  it("says which removes failed", async () => {
    removeMutateAsync.mockImplementation(({ evalConfigId }) =>
      evalConfigId === "c2"
        ? Promise.reject({ detail: "Still building." })
        : Promise.resolve(),
    );
    setup();

    fireEvent.click(screen.getByRole("checkbox", { name: "Evals (3)" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove 2" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Remove 2 evals?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove 2" }));

    await waitFor(() => expect(enqueueSnackbar).toHaveBeenCalled());
    expect(enqueueSnackbar.mock.calls[0][0]).toBe(
      "tone_check: Still building.",
    );
    expect(enqueueSnackbar.mock.calls[0][1]).toEqual({ variant: "error" });
  });

  it("falls back to the run test's own mapping when the environment row isn't given", () => {
    setup({ appliedEvals: undefined });

    fireEvent.click(
      screen.getByRole("button", { name: "Show details for tone_check" }),
    );
    expect(screen.getByText("input")).toBeInTheDocument();
    expect(screen.getByText("transcript")).toBeInTheDocument();
    expect(screen.getByText("Description soon")).toBeInTheDocument();
  });

  it("says when an expanded eval maps nothing", () => {
    setup();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Show details for refund_issued_claim",
      }),
    );
    expect(
      screen.getByText(
        "Nothing mapped. This evaluation reads the run as a whole.",
      ),
    ).toBeInTheDocument();
  });
});
