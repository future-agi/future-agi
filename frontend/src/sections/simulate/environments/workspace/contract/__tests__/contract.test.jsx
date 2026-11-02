import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import RlContractPanel from "../RlContractPanel";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

// A built voice environment as the detail mapper shapes it: rules from
// hard_constraints, use cases from real_use_cases, tools with arg_values,
// end_conditions and settings.seed.
const voiceEnv = {
  name: "Ride line",
  surface: "voice",
  tools: [
    {
      name: "geocode_address",
      desc: "Find an address.",
      args: ["query", "address_kind"],
      argTypes: { query: "str", address_kind: "str" },
      argValues: { address_kind: ["pickup", "dropoff"] },
    },
    { name: "end_call", desc: "End the call.", args: [] },
  ],
  rules: ["Never invent a fare.", "Never accept a full card number."],
  useCases: ["Book a ride for a recognized caller.", "Cancel a ride and quote the fee."],
  amendments: [{ field: "x", reason: "y" }],
  endConditions: {
    max_turns: null,
    max_duration_seconds: 3600,
    clock: "real-time",
    ended_reasons: ["customer-ended-call", "silence-timed-out"],
  },
  runSeed: 8537093545867,
};

const renderPanel = (props = {}, route = "/x") =>
  renderWithRouter(
    <RlContractPanel
      env={voiceEnv}
      envState={{ agent: { name: "Ride agent" } }}
      patch={vi.fn()}
      onGo={vi.fn()}
      backed
      {...props}
    />,
    { route },
  );

beforeEach(() => enqueueSnackbar.mockClear());

describe("Contract tab", () => {
  it("rules list the real hard_constraints with R1..RN ids; coverage is a soon chip", () => {
    renderPanel();
    expect(screen.getByText("Rules (2)")).toBeInTheDocument();
    expect(screen.getByText("R1")).toBeInTheDocument();
    expect(screen.getByText("Never invent a fare.")).toBeInTheDocument();
    expect(screen.getByText("R2")).toBeInTheDocument();
    expect(screen.getAllByText("From the agent's prompt").length).toBe(2);
    expect(screen.getByText("Coverage soon")).toBeInTheDocument();
    // Which sub-goals test a rule isn't reported, so no invented coverage.
    expect(screen.queryByText(/Tested by/)).toBeNull();
    expect(screen.queryByText(/Not tested/)).toBeNull();
  });

  it("Add rule opens the dialog flagged UI only; Create sends nothing and the list is unchanged", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Add rule")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("UI only")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Rule"), { target: { value: "Always confirm the pickup." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create the next environment version" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", { variant: "info" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Rules (2)")).toBeInTheDocument();
    expect(screen.queryByText("Always confirm the pickup.")).toBeNull();
  });

  it("Edit and Remove on a rule open the same dialog", () => {
    renderPanel();
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Remove rule R2")).toBeInTheDocument();
    expect(within(dialog).getByText("Never accept a full card number.")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    expect(within(screen.getByRole("dialog")).getByLabelText("Rule")).toHaveValue("Never invent a fare.");
  });

  it("use cases render here with ids; amendments stay on Overview", () => {
    renderPanel();
    expect(screen.getByText("Use cases (2)")).toBeInTheDocument();
    expect(screen.getAllByText(/^UC-[0-9a-z]{4}$/).length).toBe(2);
    expect(screen.getByText("Book a ride for a recognized caller.")).toBeInTheDocument();
    expect(screen.queryByText(/Amendments/)).toBeNull();
  });

  it("argument limits list the real allowed values; ranges are a soon chip", () => {
    const onGo = vi.fn();
    renderPanel({ onGo });
    expect(screen.getByText("Argument limits (1)")).toBeInTheDocument();
    expect(screen.getByText("Ranges soon")).toBeInTheDocument();
    expect(screen.getByText("pickup")).toBeInTheDocument();
    expect(screen.getByText("geocode_address.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View tools" }));
    expect(onGo).toHaveBeenCalledWith("world");
  });

  it('end conditions read "When a conversation ends", "It ends", "It\'s cut off", "Time", and the seed', () => {
    renderPanel();
    expect(screen.getByText("When a conversation ends")).toBeInTheDocument();
    expect(screen.getByText("It ends")).toBeInTheDocument();
    expect(screen.getByText("It's cut off")).toBeInTheDocument();
    expect(screen.getByText("customer-ended-call")).toBeInTheDocument();
    expect(screen.getByText("60 minutes")).toBeInTheDocument();
    expect(screen.getByText("Time · Real time")).toBeInTheDocument();
    expect(screen.getByText("Same starting data every time")).toBeInTheDocument();
    expect(screen.getByText(/Seed 8537093545867\./)).toBeInTheDocument();
  });

  it("leaves out a seed JSON has already rounded", () => {
    renderPanel({ env: { ...voiceEnv, runSeed: JSON.parse("195790417783341077") } });
    expect(screen.getByText("Same starting data every time")).toBeInTheDocument();
    expect(screen.queryByText(/Seed \d/)).toBeNull();
  });

  it("says the end conditions are not authored yet instead of showing sample ones", () => {
    renderPanel({ env: { ...voiceEnv, endConditions: null } });
    expect(screen.getByText("When a conversation ends")).toBeInTheDocument();
    expect(screen.getByText("Not authored yet")).toBeInTheDocument();
    expect(screen.queryByText("It ends")).toBeNull();
  });

  it("Actors shows the empty state; Create actor opens the editor flagged UI only and sends nothing", () => {
    renderPanel();
    expect(screen.getByText("No actors yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create actor" }));
    const drawer = screen.getByRole("presentation");
    expect(within(drawer).getAllByLabelText("UI only").length).toBeGreaterThan(0);
    fireEvent.click(within(drawer).getAllByRole("button", { name: "Create actor" }).pop());
    expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", { variant: "info" });
  });

  it("?uiSample=actors shows Vel's cast, flagged", () => {
    renderPanel({}, "/x?uiSample=actors");
    expect(screen.queryByText("No actors yet")).toBeNull();
    expect(screen.getByText(/In this environment \(\d+\)/)).toBeInTheDocument();
  });

  it("no longer holds the world's tables, tools or capability graph: they live on World", () => {
    renderPanel();
    expect(screen.queryByText(/World state/)).toBeNull();
    expect(screen.queryByText("Capability graph")).toBeNull();
  });

  it("locks edits on a template until it's forked", () => {
    renderPanel({ locked: true });
    expect(screen.queryByRole("button", { name: "Add rule" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("uses no em dashes", () => {
    renderPanel();
    expect(document.body.textContent).not.toMatch(/—/);
  });
});
