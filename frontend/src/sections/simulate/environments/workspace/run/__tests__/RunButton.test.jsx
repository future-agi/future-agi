import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import {
  render,
  screen,
  within,
  renderHook,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { SnackbarProvider } from "notistack";

const useMyEnvironments = vi.fn();
vi.mock(
  "src/api/simulate-environments/environments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useMyEnvironments: (...args) => useMyEnvironments(...args),
  }),
);

const { default: RunButton } = await import("../RunButton");
const { default: useRunRepeats } = await import("../useRunRepeats");
const { default: TrialsPicker } = await import("../../scenarios/TrialsPicker");

const ENV = {
  id: "env-1",
  name: "Refund line",
  surface: "voice",
  testSubject: { id: "agent-1" },
};
const OTHERS = [
  { id: "env-1", name: "Refund line", status: "ready" },
  { id: "env-2", name: "Billing line", status: "passed" },
  { id: "env-3", name: "Still building", status: "building" },
];

const renderRun = (props = {}, url = "/x") =>
  render(
    <SnackbarProvider>
      <MemoryRouter initialEntries={[url]}>
        <RunButton
          env={ENV}
          scenarioCount={4}
          repeats={3}
          canRun
          onStartRun={vi.fn()}
          {...props}
        />
      </MemoryRouter>
    </SnackbarProvider>,
  );

beforeEach(() => {
  useMyEnvironments.mockReset();
  useMyEnvironments.mockReturnValue({
    data: { rows: OTHERS },
    isLoading: false,
  });
});

describe("useRunRepeats", () => {
  it("defaults to 3 and keeps the choice in the environment's state", () => {
    const patch = vi.fn();
    const { result, rerender } = renderHook(
      ({ s }) => useRunRepeats(s, patch),
      {
        initialProps: { s: {} },
      },
    );
    expect(result.current[0]).toBe(3);
    act(() => result.current[1](5));
    expect(patch).toHaveBeenCalledWith({ runRepeats: 5 });
    rerender({ s: { runRepeats: 5 } });
    expect(result.current[0]).toBe(5);
  });
});

describe("TrialsPicker", () => {
  const Wrapper = ({ unit }) => (
    <TrialsPicker trials={3} onChange={vi.fn()} scenarioCount={2} unit={unit} />
  );
  Wrapper.propTypes = { unit: PropTypes.string };

  it("reads 3× with the word on hover and in its name", () => {
    render(<Wrapper />);
    expect(
      screen.getByRole("button", { name: "Repeats: 3" }),
    ).toHaveTextContent("3×");
  });

  it.each([
    ["call", "2 scenarios × 3 repeats = 6 calls"],
    ["conversation", "2 scenarios × 3 repeats = 6 conversations"],
  ])("counts %ss in the menu", async (unit, line) => {
    const user = userEvent.setup();
    render(<Wrapper unit={unit} />);
    await user.click(screen.getByRole("button", { name: "Repeats: 3" }));
    expect(screen.getByText(line)).toBeInTheDocument();
  });
});

describe("RunButton", () => {
  it("runs every scenario with the shared repeats", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    renderRun({ onStartRun });
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, 3);
  });

  it("says what a run is in its tooltip, with the estimate still to come", async () => {
    const user = userEvent.setup();
    renderRun();
    await user.hover(screen.getByRole("button", { name: "Run simulation" }));
    expect(
      await screen.findByText("4 scenarios × 3 repeats = 12 calls"),
    ).toBeInTheDocument();
    expect(screen.getByText("Estimate soon")).toBeInTheDocument();
  });

  it("offers the agent's other ready environments, flagged UI only", async () => {
    const user = userEvent.setup();
    renderRun();
    await user.click(screen.getByRole("button", { name: "More ways to run" }));
    const menu = screen.getByRole("menu");
    expect(
      within(menu).getByText("Run in more environments"),
    ).toBeInTheDocument();
    expect(within(menu).getByText("UI only")).toBeInTheDocument();
    expect(
      within(menu).getByRole("button", { name: "Billing line" }),
    ).toBeEnabled();
    expect(
      within(menu).getByRole("button", { name: "Still building" }),
    ).toBeDisabled();
    expect(
      within(menu).queryByRole("button", { name: "Refund line" }),
    ).toBeNull();
    expect(useMyEnvironments).toHaveBeenCalledWith(
      expect.objectContaining({
        filters: expect.objectContaining({ agentId: "agent-1" }),
      }),
    );
  });

  it("reads Run in 2 with one picked, and runs only here, saying the rest is UI only", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    renderRun({ onStartRun });
    await user.click(screen.getByRole("button", { name: "More ways to run" }));
    await user.click(
      within(screen.getByRole("menu")).getByRole("button", {
        name: "Billing line",
      }),
    );
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Run in 2" }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, 3);
    expect(
      await screen.findByText(/Running in other environments is UI only/),
    ).toBeInTheDocument();
  });

  it("asks first with ?uiSample=toolfit: Run anyway runs, Rebuild and run is UI only", async () => {
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    renderRun({ onStartRun }, "/x?uiSample=toolfit");
    await user.click(screen.getByRole("button", { name: "Run simulation" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/can't answer/)).toBeInTheDocument();
    expect(within(dialog).getByText("UI only")).toBeInTheDocument();
    expect(onStartRun).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: /anyway/ }));
    expect(onStartRun).toHaveBeenCalledWith(undefined, 3);
  });

  it("disables Run with the reason when the environment can't run", async () => {
    renderRun({ canRun: false, runBlockedReason: "Still building" });
    expect(
      screen.getByRole("button", { name: "Run simulation" }),
    ).toBeDisabled();
  });
});
