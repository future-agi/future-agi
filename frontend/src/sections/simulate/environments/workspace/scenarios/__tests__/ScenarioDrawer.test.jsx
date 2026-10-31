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
  it("shows every table column, in Vel's order and labels, as the API sends it", () => {
    open();
    expect(screen.getByText("pricing_objection_handled")).toBeInTheDocument();
    expect(screen.getByText("Scenario")).toBeInTheDocument();
    // Vel's labels: Passes when = Ideal outcome, Caller = Persona, Brief =
    // Situation. "Sub-goals" also heads a figure; the row is the last one.
    const labels = ["#", "Passes when", "Caller", "Brief", "Sub-goals"].map((l) => screen.getAllByText(l).at(-1));
    labels.slice(1).forEach((el, i) =>
      expect(labels[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(),
    );
    expect(screen.getByText("3")).toBeInTheDocument();
    // Passes when = `tests`, word for word (no sentence rewriting).
    expect(screen.getByText("the agent explains the price and offers the cheaper plan")).toBeInTheDocument();
    // Caller = name · gender · age group, one line, as the table writes it.
    expect(screen.getByText("Marcus Chen")).toBeInTheDocument();
    expect(screen.getByText(/· Male · 40-50/)).toBeInTheDocument();
    // Same caller line as the table: no personality.
    expect(screen.queryByText(/Cautious/)).toBeNull();
    // Vel's initials badge sits beside the name.
    expect(screen.getByText("MC")).toBeInTheDocument();
    // Brief = `instruction`, unsplit.
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
    expect(screen.queryByText("Passes when")).toBeNull();
    expect(screen.queryByText("Caller")).toBeNull();
    expect(screen.queryByText("Brief")).toBeNull();
    // Only the figure, reading 0; no Sub-goals row.
    expect(screen.getAllByText("Sub-goals")).toHaveLength(1);
    expect(screen.getByText("None set")).toBeInTheDocument();
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
