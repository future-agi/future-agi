import { describe, it, expect } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ContractTab from "../tabs/ContractTab";

const contract = {
  agent: "drive_thru",
  one_liner: "takes drive-thru orders and settles payment",
  tools: [
    {
      name: "place_order",
      description: "adds an item to the open ticket",
      args: ["item", "size"],
      arg_types: { item: "str", size: "str" },
      arg_values: { size: ["small", "large"] },
    },
    { name: "close_ticket", description: "ends the order", args: [] },
  ],
  dependencies: [
    {
      name: "menu",
      kind: "table",
      what: "every item that can be ordered",
      used_by: ["place_order"],
    },
  ],
  hard_constraints: ["never charge before the ticket is closed"],
  real_use_cases: ["a customer orders two drinks and pays"],
  anti_hallucination: ["loyalty_points", "refund_order"],
  open_questions: ["does the menu vary by store?"],
  amendments: ["dropped upsell_combo — no such tool in the source"],
};

describe("ContractTab", () => {
  it("says nothing has been read yet when the payload is empty", () => {
    render(<ContractTab contract={{}} />);
    expect(screen.getByText(/nothing yet/i)).toBeInTheDocument();
  });

  it("treats a missing payload the same way", () => {
    render(<ContractTab contract={null} />);
    expect(screen.getByText(/nothing yet/i)).toBeInTheDocument();
  });

  it("still shows a half-written contract rather than calling it empty", () => {
    render(<ContractTab contract={{ one_liner: "takes orders" }} />);
    expect(screen.queryByText(/nothing yet/i)).not.toBeInTheDocument();
    expect(screen.getByText("The whole contract")).toBeInTheDocument();
  });

  it("leads with the agent and its one-liner", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText("drive_thru")).toBeInTheDocument();
    expect(screen.getByText("takes drive-thru orders and settles payment")).toBeInTheDocument();
  });

  it("counts the tools it really has", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText(/^2 the agent really has/)).toBeInTheDocument();
  });

  it("shows a tool's arguments in its summary, and says so when it has none", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText("item, size")).toBeInTheDocument();
    expect(screen.getByText("no arguments")).toBeInTheDocument();
  });

  it("gives each argument its type and its permitted values", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText("Argument")).toBeInTheDocument();
    expect(screen.getByText("Permitted values")).toBeInTheDocument();
    expect(screen.getByText("small, large")).toBeInTheDocument();
  });

  it("lists what the environment stage must build", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText("What it depends on")).toBeInTheDocument();
    expect(screen.getByText("every item that can be ordered")).toBeInTheDocument();
    expect(screen.getByText("place_order", { selector: "td" })).toBeInTheDocument();
  });

  it("shows the rules, use cases, banned names and open questions", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText("never charge before the ticket is closed")).toBeInTheDocument();
    expect(screen.getByText("a customer orders two drinks and pays")).toBeInTheDocument();
    expect(screen.getByText("Does not exist")).toBeInTheDocument();
    expect(screen.getByText("loyalty_points")).toBeInTheDocument();
    expect(screen.getByText("does the menu vary by store?")).toBeInTheDocument();
  });

  it("shows each amendment with its reason", () => {
    render(<ContractTab contract={contract} />);
    expect(screen.getByText("dropped upsell_combo — no such tool in the source")).toBeInTheDocument();
  });

  it("keeps the raw contract at the bottom, foldable", async () => {
    const { container } = render(<ContractTab contract={contract} />);
    expect(screen.getByText("The whole contract")).toBeInTheDocument();
    expect(screen.getByText("contract.json, as written")).toBeInTheDocument();

    // The tree is the only thing on the tab offering these controls.
    await userEvent.click(screen.getByRole("button", { name: "collapse all" }));
    const folds = [...container.querySelectorAll("details")].filter((node) =>
      node.querySelector("summary")?.textContent.match(/[{[]\d/)
    );
    expect(folds.length).toBeGreaterThan(0);
    expect(folds.every((node) => !node.open)).toBe(true);
  });

  it("does not draw panes for the sections the contract left out", () => {
    render(<ContractTab contract={{ agent: "bare", tools: [] }} />);
    expect(screen.queryByText("Hard rules")).not.toBeInTheDocument();
    expect(screen.queryByText("Amendments")).not.toBeInTheDocument();
    expect(screen.getByText(/^0 the agent really has/)).toBeInTheDocument();
  });
});
