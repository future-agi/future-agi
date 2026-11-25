import { describe, it, expect } from "vitest";

import { buildTraceFilterFields, toServerFilters } from "../traceFilters";

const facet = (...values) => values.map((value) => ({ value, count: 1 }));

describe("buildTraceFilterFields", () => {
  it("offers the Scenarios tab's fields in its order and with its labels", () => {
    const fields = buildTraceFilterFields({
      goal: facet("Refund"),
      sub_goal: facet("verified"),
      persona: facet("Ana"),
      age: facet("adult"),
      attack: facet("none"),
      task: facet("lookup"),
    });

    expect(fields.map((f) => [f.value, f.label, f.type])).toEqual([
      ["scenario", "Scenario", "string"],
      ["goal", "Use case", "enum"],
      ["situation", "Situation", "string"],
      ["persona", "Name", "enum"],
      ["age", "Age", "enum"],
      ["attack", "Attack", "enum"],
      ["task", "Task", "enum"],
      ["subGoal", "Sub-goal", "enum"],
      ["status", "Status", "enum"],
    ]);
    const byValue = Object.fromEntries(fields.map((f) => [f.value, f]));
    expect(byValue.persona.choices).toEqual(["Ana"]);
    expect(byValue.task.choices).toEqual(["lookup"]);
  });

  it("lets the text fields only contain, since the server has no negation", () => {
    const fields = buildTraceFilterFields();
    const text = fields.filter((f) => f.type === "string");

    expect(text.map((f) => f.value)).toEqual(["scenario", "situation"]);
    text.forEach((f) => expect(f.operators).toEqual(["contains"]));
  });

  it("hides an enum field whose facet has no values", () => {
    const fields = buildTraceFilterFields({
      goal: [],
      sub_goal: [],
      persona: [],
      age: facet("adult"),
    });

    expect(fields.map((f) => f.value)).toEqual([
      "scenario",
      "situation",
      "age",
      "status",
    ]);
  });

  it("names the coverage and sub-goal choices with the server's labels", () => {
    const labels = { none: "No attack", lookup: "Lookup" };
    const fields = buildTraceFilterFields(
      {
        goal: facet("Refund"),
        sub_goal: facet("verified"),
        persona: facet("Ana"),
        age: facet("adult"),
        attack: facet("none"),
        task: facet("lookup"),
      },
      labels,
    );
    const byValue = Object.fromEntries(fields.map((f) => [f.value, f]));

    ["attack", "task", "subGoal"].forEach((key) =>
      expect(byValue[key].choiceLabels).toBe(labels),
    );
    ["goal", "persona", "age"].forEach((key) =>
      expect(byValue[key].choiceLabels).toBeUndefined(),
    );
  });
});

describe("toServerFilters", () => {
  it("passes the Scenarios tab's filters through, with each enum's negation", () => {
    expect(
      toServerFilters({
        scenario: ["refund"],
        situation: ["case"],
        persona: ["Ana"],
        persona_not: ["Bo"],
        age: ["adult"],
        age_not: ["teen"],
        attack: ["none"],
        attack_not: ["injection"],
        task: ["lookup"],
        task_not: ["escalation"],
        subGoal: ["verified"],
      }),
    ).toEqual({
      scenario: ["refund"],
      situation: ["case"],
      persona: ["Ana"],
      persona_not: ["Bo"],
      age: ["adult"],
      age_not: ["teen"],
      attack: ["none"],
      attack_not: ["injection"],
      task: ["lookup"],
      task_not: ["escalation"],
      sub_goal: ["verified"],
    });
  });

  it("drops empty and unknown keys", () => {
    expect(
      toServerFilters({ scenario: [], persona: [], situation_not: ["x"] }),
    ).toEqual({});
  });
});
