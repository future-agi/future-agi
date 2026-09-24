import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  getAvailableEvaluations: vi.fn(),
  addEvaluation: vi.fn(() => Promise.resolve({ evaluations: { selected: [] } })),
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  getHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(),
}));

const { getAvailableEvaluations, addEvaluation, getHarnessEnvironment } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);
const { default: AddEvaluationDrawer } = await import("../AddEvaluationDrawer");

const render = (ui) =>
  rtlRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

const VOICE_ENV = { id: "env-1", agentType: "voice" };
const OFFERS = {
  evaluations: [
    {
      name: "advice_authority_boundary",
      description: "Stays within its granted authority",
      required_keys: ["agent_prompt", "conversation"],
      modality: "voice",
    },
  ],
};

describe("AddEvaluationDrawer (§10)", () => {
  beforeEach(() => {
    getAvailableEvaluations.mockReset();
    getAvailableEvaluations.mockResolvedValue(OFFERS);
    getHarnessEnvironment.mockReset();
    getHarnessEnvironment.mockResolvedValue({ evaluations: { selected: [] } });
    addEvaluation.mockClear();
  });

  it("lists evals as rows; the mapping is hidden until the row is expanded", async () => {
    render(<AddEvaluationDrawer open env={VOICE_ENV} onClose={vi.fn()} />);

    // The row shows the eval name; the mapping/detail is collapsed.
    expect(await screen.findByText("advice_authority_boundary")).toBeInTheDocument();
    expect(screen.queryByText("voice_recording")).toBeNull();

    // Expand the row → description + read-only mapping (key → resolved source).
    fireEvent.click(screen.getByText("advice_authority_boundary"));
    expect(screen.getByText("Stays within its granted authority")).toBeInTheDocument();
    expect(screen.getByText("{{conversation}}")).toBeInTheDocument();
    expect(screen.getByText("voice_recording")).toBeInTheDocument();

    // Read-only: no editable select/combobox in the mapping.
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("adds by name only (POST { name }) — mapping is resolved server-side", async () => {
    render(<AddEvaluationDrawer open env={VOICE_ENV} onClose={vi.fn()} />);
    await screen.findByText("advice_authority_boundary");

    fireEvent.click(screen.getByRole("button", { name: /Add/i }));

    await waitFor(() =>
      expect(addEvaluation).toHaveBeenCalledWith("env-1", "advice_authority_boundary"),
    );
  });

  it("shows an 'Added' (disabled) state for an eval already in §6 selected", async () => {
    getHarnessEnvironment.mockResolvedValue({
      evaluations: { selected: [{ id: "cfg-1", name: "advice_authority_boundary" }] },
    });
    render(<AddEvaluationDrawer open env={VOICE_ENV} onClose={vi.fn()} />);

    const btn = await screen.findByRole("button", { name: /Added/i });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(addEvaluation).not.toHaveBeenCalled();
  });

  it("warns and disables Add at the 8-eval cap", async () => {
    getHarnessEnvironment.mockResolvedValue({
      evaluations: { selected: Array.from({ length: 8 }, (_, i) => ({ id: `c${i}`, name: `eval_${i}` })) },
    });
    render(<AddEvaluationDrawer open env={VOICE_ENV} onClose={vi.fn()} />);

    expect(await screen.findByText(/maximum 8 evaluations/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText("advice_authority_boundary"));
    // Add is disabled at the cap and does not fire.
    expect(screen.getByRole("button", { name: /Add/i })).toBeDisabled();
  });

  it("shows the empty state when nothing is left to add", async () => {
    getAvailableEvaluations.mockResolvedValue({ evaluations: [] });
    render(<AddEvaluationDrawer open env={VOICE_ENV} onClose={vi.fn()} />);
    expect(await screen.findByText(/Nothing left to add/i)).toBeInTheDocument();
  });
});
