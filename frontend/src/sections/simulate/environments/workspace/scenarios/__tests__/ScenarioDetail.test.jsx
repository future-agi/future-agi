import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ScenarioDetail, { ScenarioDetailBody } from "../ScenarioDetail";

const row = (subTasks) => ({ id: "sc-1", name: "Refund request", task: "Ask for a refund.", subTasks });

describe("ScenarioDetail sub-goals", () => {
  // The scenarios list endpoint sends sub_goals as plain name strings.
  it("shows each sub-goal's text when the list sends plain strings", () => {
    render(<ScenarioDetail row={row(["identity_verified", "refund_created"])} defaultOpen />);
    expect(screen.getByText("Sub-goals: the moves that settle it (2)")).toBeInTheDocument();
    expect(screen.getByText("identity_verified")).toBeInTheDocument();
    expect(screen.getByText("refund_created")).toBeInTheDocument();
  });

  it("shows each sub-goal's label when they arrive as objects", () => {
    render(<ScenarioDetail row={row([{ id: "a", label: "Verify identity" }])} defaultOpen />);
    expect(screen.getByText("Verify identity")).toBeInTheDocument();
  });

  it("skips empty sub-goals rather than numbering a blank line", () => {
    render(<ScenarioDetail row={row(["identity_verified", "", null])} defaultOpen />);
    expect(screen.getByText("Sub-goals: the moves that settle it (1)")).toBeInTheDocument();
  });
});

describe("ScenarioDetail persona", () => {
  it("lists the persona facts the API serves and skips the empty ones", () => {
    render(
      <ScenarioDetail
        defaultOpen
        row={{
          ...row([]),
          persona: {
            name: "Alex Morgan",
            ageGroup: "25-32",
            occupation: "Marketing lead",
            location: "Austin",
            personality: "Direct",
            communicationStyle: "Brief",
            languages: ["English", "Spanish"],
            accent: null,
          },
        }}
      />,
    );
    expect(screen.getByText("Marketing lead")).toBeInTheDocument();
    expect(screen.getByText("Austin")).toBeInTheDocument();
    expect(screen.getByText("Direct")).toBeInTheDocument();
    expect(screen.getByText("Brief")).toBeInTheDocument();
    expect(screen.getByText("English, Spanish")).toBeInTheDocument();
    expect(screen.queryByText("Accent")).toBeNull();
    expect(screen.queryByText("Gender")).toBeNull();
  });
});

describe("ScenarioDetailBody", () => {
  it("shows the branch once when the category says the same", () => {
    render(<ScenarioDetailBody row={{ ...row([]), branchCategory: "Price objection", conversationBranch: "Price objection" }} />);
    expect(screen.queryByText("Category")).toBeNull();
    expect(screen.getByText("Branch")).toBeInTheDocument();
  });
});

describe("ScenarioDetailBody — empty scenario", () => {
  it("leaves out sections with nothing in them", () => {
    render(<ScenarioDetailBody row={{ id: "x", name: "orphan", subTasks: [] }} />);
    expect(screen.queryByText("What this tests")).toBeNull();
    expect(screen.queryByText("The person is told")).toBeNull();
  });
});
