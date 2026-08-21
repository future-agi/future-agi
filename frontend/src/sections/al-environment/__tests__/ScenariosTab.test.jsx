import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ScenariosTab from "../tabs/ScenariosTab";

// vi.mock is hoisted above every const, so the shared spy has to be hoisted with it.
const mocked = vi.hoisted(() => ({ fetchScenarioFile: vi.fn() }));

vi.mock("src/api/al-environment/alEnvironment", () => ({
  fetchScenarioFile: mocked.fetchScenarioFile,
}));

// Shapes taken from /api/scenarios, which answers with a bare array of these.
const validatedOne = {
  name: "plain_order",
  use_case: "ordering",
  instruction: "Order a latte.",
  tests: "that the happy path still works",
  validated: true,
  why: "",
  gates: { ready: true, solvable: true, not_vacuous: true },
  variables: { item: "latte" },
  solution: [{ tool: "place_order", arguments: { item: "latte" } }],
  checks: [
    { name: "order_placed", settled_by: "code", what: "an order row exists", source: "" },
    { name: "was_polite", settled_by: "a judge", what: "the reply is courteous", source: "" },
  ],
  files: ["scenario.json", "checks/order_placed.py"],
  folder: "/tmp/plain_order",
};

const brokenOne = {
  name: "unknown_item",
  instruction: "Order something not on the menu.",
  validated: false,
  why: "the reference solution did not pass its own checks",
  gates: { ready: true, solvable: false },
  variables: {},
  solution: [],
  checks: [],
  files: [],
  folder: "/tmp/unknown_item",
};

const uncheckedOne = {
  name: "refund_flow",
  instruction: "Ask for a refund.",
  validated: null,
  why: "no world to check against yet",
  gates: {},
  variables: {},
  solution: [],
  checks: [],
  files: [],
  folder: "/tmp/refund_flow",
};

// A <summary> carries no implicit button role in jsdom, and a shut <details> keeps its body
// in the DOM — so open/shut is only ever read off the element's own attribute.
const cardFor = (container, name) =>
  [...container.querySelectorAll("details")].find((one) => one.textContent.includes(name));

// Same scenario, failing its gates — so its card auto-opens and its file chips are reachable.
const withFiles = { ...validatedOne, validated: false, why: "" };

describe("ScenariosTab", () => {
  it("says none are written yet and offers to write some once a world exists", async () => {
    const onSay = vi.fn();
    render(<ScenariosTab scenarios={[]} onSay={onSay} hasWorld />);
    expect(screen.getByText(/none written yet/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "write 5 scenarios" }));
    expect(onSay).toHaveBeenCalledWith("write 5 scenarios for this agent");
  });

  it("withholds the write chip until there is a world to check against", () => {
    render(<ScenariosTab scenarios={[]} onSay={vi.fn()} hasWorld={false} />);
    expect(screen.queryByRole("button", { name: "write 5 scenarios" })).not.toBeInTheDocument();
  });

  it("counts how many are validated", () => {
    render(<ScenariosTab scenarios={[validatedOne, brokenOne, uncheckedOne]} />);
    expect(screen.getByText(/1 of 3 validated — only these are ever run/)).toBeInTheDocument();
  });

  it("names each of the three verdicts the way the harness does", () => {
    render(<ScenariosTab scenarios={[validatedOne, brokenOne, uncheckedOne]} />);
    expect(screen.getByText("validated")).toBeInTheDocument();
    expect(screen.getByText("not ready")).toBeInTheDocument();
    expect(screen.getByText("unchecked")).toBeInTheDocument();
  });

  it("lamps every gate that held, failed or never ran", () => {
    render(<ScenariosTab scenarios={[brokenOne]} />);
    expect(screen.getByTitle("world is ready: holds")).toHaveTextContent("✓");
    expect(screen.getByTitle("solution passes: fails")).toHaveTextContent("✗");
    expect(screen.getByTitle("checks can fail: unknown")).toHaveTextContent("?");
  });

  it("opens a scenario that is not validated and leaves a good one shut", () => {
    const { container } = render(<ScenariosTab scenarios={[validatedOne, brokenOne]} />);
    expect(cardFor(container, "plain_order")).not.toHaveAttribute("open");
    expect(cardFor(container, "unknown_item")).toHaveAttribute("open");
  });

  it("shows why a scenario is not ready", () => {
    render(<ScenariosTab scenarios={[brokenOne]} />);
    expect(
      screen.getByText("the reference solution did not pass its own checks")
    ).toBeInTheDocument();
  });

  it("carries the instruction, what it tests, its slots and its solution", () => {
    render(<ScenariosTab scenarios={[validatedOne]} />);
    expect(screen.getByText("Order a latte.")).toBeInTheDocument();
    expect(screen.getByText("that the happy path still works")).toBeInTheDocument();
    expect(screen.getByText("item")).toBeInTheDocument();
    expect(screen.getByText("place_order")).toBeInTheDocument();
    expect(screen.getByText('{"item":"latte"}')).toBeInTheDocument();
    expect(screen.getByText("1-step solution · 2 checks")).toBeInTheDocument();
  });

  it("splits checks settled by code from those settled by a judge", () => {
    render(<ScenariosTab scenarios={[validatedOne]} />);
    expect(screen.getByTitle("an order row exists")).toHaveTextContent("order_placed");
    expect(screen.getByTitle("the reply is courteous")).toHaveTextContent("was_polite");
  });

  it("marks a scenario that has already been run", () => {
    render(
      <ScenariosTab
        scenarios={[validatedOne]}
        runs={[{ scenario: "plain_order", passed: false }]}
      />
    );
    expect(screen.getByText("ran: fail")).toBeInTheDocument();
  });

  it("offers a jump to the run a scenario already has", async () => {
    const onSeeRun = vi.fn();
    render(
      <ScenariosTab
        scenarios={[validatedOne]}
        runs={[{ scenario: "plain_order", passed: true }]}
        onSeeRun={onSeeRun}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /see its run/ }));
    expect(onSeeRun).toHaveBeenCalled();
  });

  it("withholds the jump when the scenario has never run", () => {
    render(<ScenariosTab scenarios={[validatedOne]} runs={[]} onSeeRun={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /see its run/ })).not.toBeInTheDocument();
  });

  it("reveals a file's source when its chip is clicked", async () => {
    mocked.fetchScenarioFile.mockResolvedValue({ source: "def check(world): ..." });
    // A card the reader can actually reach the chips in: not validated, so it starts open.
    const { container } = render(<ScenariosTab scenarios={[withFiles]} />);
    expect(cardFor(container, "plain_order")).toHaveAttribute("open");

    await userEvent.click(screen.getByRole("button", { name: "checks/order_placed.py" }));

    expect(mocked.fetchScenarioFile).toHaveBeenCalledWith("plain_order", "checks/order_placed.py");
    expect(await screen.findByText("def check(world): ...")).toBeInTheDocument();
  });

  it("shows the fetch's error in place of the source", async () => {
    mocked.fetchScenarioFile.mockResolvedValue({ error: "no such file" });
    render(<ScenariosTab scenarios={[withFiles]} />);

    await userEvent.click(screen.getByRole("button", { name: "scenario.json" }));

    expect(await screen.findByText("no such file")).toBeInTheDocument();
  });
});
