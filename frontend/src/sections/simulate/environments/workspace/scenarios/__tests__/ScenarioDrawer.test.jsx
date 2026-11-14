import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { scenarioFromApi } from "src/api/simulate-environments/scenarios";
import ScenarioDrawer from "../ScenarioDrawer";

// A row exactly as the scenarios list API sends it.
const API_ROW = {
  id: "s1",
  scenario_key: "pricing_objection_handled",
  number: 3,
  name: "pricing_objection_handled",
  use_case: "Handle customer objections during sales inquiries.",
  instruction: "You run a small 5-person team.\nAsk why the plan costs more than a rival.",
  tests: "the agent explains the price and offers the cheaper plan",
  branch: "objection",
  persona: { name: "Marcus Chen", gender: "male", age_group: "40-50", occupation: "Founder", accent: "neutral", personality: "Cautious and skeptical" },
  sub_goals: ["acknowledges_concern", "offers_alternative"],
};

const open = (apiRow = API_ROW, props = {}) =>
  render(<ScenarioDrawer row={scenarioFromApi(apiRow)} onClose={vi.fn()} onEdit={vi.fn()} {...props} />);

describe("ScenarioDrawer", () => {
  it("shows every table column, in the table's order, as the API sends it", () => {
    open();
    expect(screen.getByText("pricing_objection_handled")).toBeInTheDocument();
    expect(screen.getByText("Scenario")).toBeInTheDocument();
    const labels = ["#", "Persona", "Situation", "Sub-goals", "Ideal outcome"].map((l) => screen.getByText(l));
    labels.slice(1).forEach((el, i) =>
      expect(labels[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(),
    );
    expect(screen.getByText("3")).toBeInTheDocument();
    // Ideal outcome = `tests`, word for word (no sentence rewriting).
    expect(screen.getByText("the agent explains the price and offers the cheaper plan")).toBeInTheDocument();
    // Persona = name · gender · age group, one line, as the table writes it.
    expect(screen.getByText("Marcus Chen")).toBeInTheDocument();
    expect(screen.getByText(/· Male · 40-50/)).toBeInTheDocument();
    // Same caller line as the table: no personality.
    expect(screen.queryByText(/Cautious/)).toBeNull();
    // The name is shown in full, so no initials badge.
    expect(screen.queryByText("MC")).toBeNull();
    // Situation = `instruction`, unsplit.
    expect(screen.getByText(/You run a small 5-person team\.\s+Ask why the plan costs more than a rival\./)).toBeInTheDocument();
    // Sub-goals, numbered, in the order sent.
    expect(screen.getByText("acknowledges_concern")).toBeInTheDocument();
    expect(screen.getByText("2.")).toBeInTheDocument();
  });

  it("leaves out what the table doesn't show", () => {
    open();
    expect(screen.queryByText("Scenario 3")).toBeNull();
    expect(screen.queryByText(API_ROW.use_case)).toBeNull();
    expect(screen.queryByText("Founder")).toBeNull();
    expect(screen.queryByText("Conversation flow")).toBeNull();
  });

  it("names a persona by role when it has no gender or age group, as the table does", () => {
    const row = scenarioFromApi({ ...API_ROW, persona: { name: "Dana Ops" } });
    row.persona.role = "IT admin";
    render(<ScenarioDrawer row={row} onClose={vi.fn()} />);
    expect(screen.getByText(/· IT admin/)).toBeInTheDocument();
  });

  it("drops a row the API left empty instead of filling it in", () => {
    open({ id: "s2", name: "bare", persona: {}, sub_goals: [] });
    expect(screen.queryByText("#")).toBeNull();
    expect(screen.queryByText("Ideal outcome")).toBeNull();
    expect(screen.queryByText("Persona")).toBeNull();
    expect(screen.queryByText("Situation")).toBeNull();
    expect(screen.queryByText("Sub-goals")).toBeNull();
    expect(screen.queryByText("Scenario")).toBeNull();
  });

  it("hands off to the editor from Edit, and hides Edit when locked", () => {
    const onEdit = vi.fn();
    const { unmount } = open(API_ROW, { onEdit });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: "s1" }));
    unmount();
    open(API_ROW, { locked: true });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
});
