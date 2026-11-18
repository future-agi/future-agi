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
  instruction:
    "You run a small 5-person team.\nAsk why the plan costs more than a rival.",
  tests: "the agent explains the price and offers the cheaper plan",
  branch: "objection",
  persona: {
    name: "Marcus Chen",
    gender: "male",
    age_group: "40-50",
    occupation: "Founder",
    accent: "neutral",
    personality: "Cautious and skeptical",
  },
  sub_goals: ["acknowledges_concern", "offers_alternative"],
};

const UNAVAILABLE = {
  action: "refund",
  tool: "refund",
  mode: "unavailable",
  mode_label: "Unavailable",
  line: "refund: Unavailable on call 1",
  on_call: 1,
  delay_seconds: null,
  status: null,
  message: "",
};

const open = (apiRow = API_ROW, props = {}) =>
  render(
    <ScenarioDrawer
      row={scenarioFromApi(apiRow)}
      onClose={vi.fn()}
      onEdit={vi.fn()}
      {...props}
    />,
  );

describe("ScenarioDrawer", () => {
  it("shows every table column, in the table's order, as the API sends it", () => {
    open();
    expect(screen.getByText("pricing_objection_handled")).toBeInTheDocument();
    expect(screen.getByText("Scenario")).toBeInTheDocument();
    const labels = [
      "#",
      "Persona",
      "Situation",
      "Sub-goals",
      "Ideal outcome",
    ].map((l) => screen.getByText(l));
    labels
      .slice(1)
      .forEach((el, i) =>
        expect(
          labels[i].compareDocumentPosition(el) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy(),
      );
    expect(screen.getByText("3")).toBeInTheDocument();
    // Ideal outcome = `tests`, word for word (no sentence rewriting).
    expect(
      screen.getByText(
        "the agent explains the price and offers the cheaper plan",
      ),
    ).toBeInTheDocument();
    // Persona = name · gender · age group, one line, as the table writes it.
    expect(screen.getByText("Marcus Chen")).toBeInTheDocument();
    expect(screen.getByText(/· Male · 40-50/)).toBeInTheDocument();
    // Same caller line as the table: no personality.
    expect(screen.queryByText(/Cautious/)).toBeNull();
    // The name is shown in full, so no initials badge.
    expect(screen.queryByText("MC")).toBeNull();
    // Situation = `instruction`, unsplit.
    expect(
      screen.getByText(
        /You run a small 5-person team\.\s+Ask why the plan costs more than a rival\./,
      ),
    ).toBeInTheDocument();
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
    // The test conditions always read, even when the scenario sets none.
    expect(screen.getByText("Hidden · test conditions")).toBeInTheDocument();
    expect(screen.getByText("Tools behave normally")).toBeInTheDocument();
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

describe("ScenarioDrawer actors", () => {
  it("says who else is in the scenario, when they enter and what they do, as the row resolves them", () => {
    open({
      ...API_ROW,
      actors: [
        {
          id: "friend",
          name: "Rowan",
          role: "a friend with the caller",
          channel: "audio_mix",
          side: "caller",
          effective_trigger: { kind: "mid_journey", turn: 2 },
          when_label: "After the agent's turn 2",
          timeout_label: null,
          instruction: "You want a second item.",
        },
      ],
    });
    expect(screen.getByText("Actors")).toBeInTheDocument();
    expect(screen.getByText("Rowan")).toBeInTheDocument();
    expect(
      screen.getByText(/a friend with the caller · After the agent's turn 2/),
    ).toBeInTheDocument();
    expect(screen.getByText("You want a second item.")).toBeInTheDocument();
  });

  it("names an actor who never speaks, with when it comes in and its wait in the server's words", () => {
    open({
      ...API_ROW,
      actors: [
        {
          id: "approver",
          name: "Avery Stone",
          role: "approves refunds",
          channel: null,
          side: "world",
          effective_trigger: { kind: "contacted" },
          when_label: "When the agent contacts them",
          timeout_label: "Waits 60 s, then it declines",
          instruction: "",
        },
      ],
    });
    expect(screen.getByText("Avery Stone")).toBeInTheDocument();
    expect(screen.queryByText("approver")).not.toBeInTheDocument();
    expect(
      screen.getByText(/approves refunds · When the agent contacts them/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Waits 60 s, then it declines"),
    ).toBeInTheDocument();
  });

  it("has no actors row for a scenario that includes none", () => {
    open();
    expect(screen.queryByText("Actors")).not.toBeInTheDocument();
  });

  it("lists the actions an actor takes in the scenario, each with when the server says the world takes it", () => {
    open({
      ...API_ROW,
      actors: [
        {
          id: "approver",
          name: "Avery Stone",
          role: "",
          when_label: "From the start",
          timeout_label: null,
          actions: [
            { action: "approvals.put", when_label: "At the start" },
            {
              action: "tickets.change",
              trigger: { kind: "on_tool", tool: "refund" },
              when_label: "When the agent calls refund",
            },
          ],
        },
      ],
    });
    expect(
      screen.getByText("Takes approvals.put · At the start"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Takes tickets.change · When the agent calls refund"),
    ).toBeInTheDocument();
  });

  it("lists a scenario's test conditions under Hidden, each as the server resolved it", () => {
    open({
      ...API_ROW,
      faults: [
        {
          action: "orders.refund",
          tool: "orders.refund",
          mode: "timeout_after_commit",
          mode_label: "Runs, then the answer is lost",
          line: "orders.refund: Runs, then the answer is lost on call 1 after 5 s",
          on_call: 1,
          delay_seconds: 5,
          status: null,
          message: "",
        },
        {
          action: "inventory.reserve",
          tool: "inventory.reserve",
          mode: "unavailable",
          mode_label: "Unavailable",
          line: "inventory.reserve: Unavailable",
          on_call: null,
          delay_seconds: null,
          status: null,
          message: "",
        },
      ],
    });
    expect(screen.getByText("Hidden · test conditions")).toBeInTheDocument();
    expect(
      screen.getByText(
        "orders.refund: Runs, then the answer is lost on call 1 after 5 s",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("inventory.reserve: Unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Tools behave normally")).toBeNull();
  });

  it("names a test condition by the tool the server resolved it to, with its route under it", () => {
    open({
      ...API_ROW,
      faults: [
        {
          action: "orders.update_status",
          tool: "update_order_status",
          mode: "error",
          mode_label: "Answers with an error",
          line: "update_order_status: Answers with an error on call 1 (503: The order status service is down)",
          on_call: 1,
          delay_seconds: null,
          status: 503,
          message: "The order status service is down",
        },
      ],
    });
    expect(
      screen.getByText(
        "update_order_status: Answers with an error on call 1 (503: The order status service is down)",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("orders.update_status")).toBeInTheDocument();
  });

  it.each([
    ["fault_unreachable", "Hidden · test conditions"],
    ["actor_no_door", "Actors"],
  ])(
    "says why the scenario cannot work as set, in the server's words, where its %s problem is",
    (code, rowLabel) => {
      const message = `The server says: ${code}`;
      open({
        ...API_ROW,
        actors: [
          {
            id: "approver",
            name: "Avery Stone",
            role: "",
            when_label: "When the agent contacts them",
            timeout_label: null,
          },
        ],
        faults: [UNAVAILABLE],
        door_problem: { code, message },
      });
      const line = screen.getByText(message);
      expect(line.closest(".sr")).toHaveTextContent(rowLabel);
    },
  );

  it("says nothing about a door when the server finds no problem", () => {
    open({
      ...API_ROW,
      faults: [UNAVAILABLE],
      door_problem: null,
    });
    expect(screen.queryByText(/cannot/)).toBeNull();
  });

  it("shows a test condition naming no tool by the server's line, never as null, with no route under it", () => {
    open({
      ...API_ROW,
      faults: [
        {
          action: "refunds.void",
          tool: null,
          mode: "unavailable",
          mode_label: "Unavailable",
          line: "refunds.void: Unavailable on call 1",
          on_call: 1,
          delay_seconds: null,
          status: null,
          message: "",
        },
      ],
    });
    expect(
      screen.getByText("refunds.void: Unavailable on call 1"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/null/)).toBeNull();
  });
});
