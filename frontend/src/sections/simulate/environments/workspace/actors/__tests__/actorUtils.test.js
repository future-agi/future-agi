import { describe, expect, it } from "vitest";
import {
  KEPT,
  changesOf,
  fallbackActs,
  fallbackKey,
  hasEdits,
  initialFields,
  parsedArguments,
  whereProblem,
  withStart,
} from "../actorUtils";
import { TRIGGER_FIELDS, cast, typedActor } from "./castFixtures";

// A new actor as the panel opens it: the cast's `new_actor` on its side.
const NEW = (side) => ({ ...cast([]).new_actor, side, channel: null });

const MANAGER = typedActor({
  id: "manager",
  name: "Priya",
  role: "sales manager",
  permitted_actions: ["crm.approve"],
  facts: ["a", "b"],
  files: ["knowledge/policy.md"],
  records: [{ store: "crm", collection: "deals" }],
  persona: { name: "Priya", gender: "female", occupation: "Engineer" },
  trigger: { kind: "after_seconds", seconds: 30 },
});

describe("changesOf", () => {
  it("sends nothing for an existing actor when nothing was edited, and a field typed back is no edit", () => {
    expect(changesOf(MANAGER, {})).toEqual({});
    const fields = { name: "Priya ", role: "sales manager" };
    expect(changesOf(MANAGER, fields)).toEqual({});
    expect(hasEdits(MANAGER, fields)).toBe(false);
    expect(hasEdits(MANAGER, { name: "Priya R." })).toBe(true);
  });

  it("sends only the edited fields, trimmed, by the request's own names", () => {
    expect(
      changesOf(MANAGER, {
        name: " Priya R. ",
        facts: "a\n\n c ",
        may_agree: "small discounts\n",
        max_turns: "3",
      }),
    ).toEqual({
      name: "Priya R.",
      facts: ["a", "c"],
      may_agree: ["small discounts"],
      max_turns: 3,
    });
  });

  it("clears a channel, parent, model and cap with null when emptied", () => {
    const speaking = typedActor({
      id: "jordan",
      channel: "audio_mix",
      parent: "manager",
      model: "small",
      max_lines: 4,
    });
    expect(
      changesOf(speaking, {
        channel: "",
        parent: "",
        model: "",
        max_lines: "",
      }),
    ).toEqual({ channel: null, parent: null, model: null, max_lines: null });
  });

  it("sends a number as typed, so the server says when it is out of range", () => {
    expect(changesOf(MANAGER, { timeout: "-5", max_turns: "2.7" })).toEqual({
      timeout_seconds: -5,
      max_turns: 2.7,
    });
  });

  it("sends the picked files and record collections whole", () => {
    expect(
      changesOf(MANAGER, {
        files: [],
        records: [
          { store: "crm", collection: "deals" },
          { store: "crm", collection: "quotes" },
        ],
      }),
    ).toEqual({
      files: [],
      records: [
        { store: "crm", collection: "deals" },
        { store: "crm", collection: "quotes" },
      ],
    });
  });

  it("sends a voice field as picked, empty for any, and loudness as set", () => {
    expect(
      changesOf(MANAGER, {
        gender: "",
        accent: "British",
        loudness_ratio: 0.5,
      }),
    ).toEqual({ gender: "", accent: "British", loudness_ratio: 0.5 });
  });

  it("sends the trigger only when how they come in really changes, built from its own fields", () => {
    expect(changesOf(MANAGER, { triggerSeconds: "30" }, TRIGGER_FIELDS)).toEqual({});
    expect(changesOf(MANAGER, { triggerSeconds: "45" }, TRIGGER_FIELDS)).toEqual({
      trigger: { kind: "after_seconds", seconds: 45 },
    });
    expect(
      changesOf(
        MANAGER,
        { mode: "on_tool", triggerTool: "crm.approve" },
        TRIGGER_FIELDS,
      ),
    ).toEqual({ trigger: { kind: "on_tool", tool: "crm.approve" } });
    expect(
      changesOf(
        MANAGER,
        { mode: "on_keyword", triggerWords: "refund, , VP" },
        TRIGGER_FIELDS,
      ),
    ).toEqual({ trigger: { kind: "on_keyword", words: ["refund", "VP"] } });
    expect(changesOf(MANAGER, { mode: "contacted" }, TRIGGER_FIELDS)).toEqual({
      trigger: { kind: "contacted" },
    });
    expect(
      changesOf(
        MANAGER,
        {
          mode: "conditional",
          triggerCollection: "approvals",
          triggerWhere: '{"status": "pending"}',
        },
        TRIGGER_FIELDS,
      ),
    ).toEqual({
      trigger: {
        kind: "conditional",
        collection: "approvals",
        where: { status: "pending" },
      },
    });
  });

  it("keeps a trigger the form cannot make, as the cast marks it, until another way is picked", () => {
    const chance = typedActor({
      id: "lead",
      trigger: { kind: "probability", p: 0.25 },
      editable_trigger: false,
    });
    expect(initialFields(chance).mode).toBe(KEPT);
    expect(changesOf(chance, { name: "Lead" })).toEqual({ name: "Lead" });
    expect(changesOf(chance, { mode: "always" })).toEqual({
      trigger: { kind: "always" },
    });
  });

  it("sends a fallback's bare value, whatever list it was picked from", () => {
    expect(
      changesOf(MANAGER, {
        fallback: fallbackKey({ kind: "action", value: "crm.approve" }),
      }),
    ).toEqual({ fallback: "crm.approve" });
    expect(
      changesOf(MANAGER, {
        fallback: fallbackKey({ kind: "actor", value: "lead_one" }),
      }),
    ).toEqual({ fallback: "lead_one" });
    expect(fallbackActs("action:crm.approve")).toBe(true);
    expect(fallbackActs("actor:crm.approve")).toBe(false);
  });

  it("starts the fallback from the cast's classified one", () => {
    const acting = typedActor({
      id: "ops",
      fallback: { kind: "action", value: "crm.approve", label: "x" },
      fallbackArguments: { set: { status: "declined" } },
    });
    expect(initialFields(acting).fallback).toBe("action:crm.approve");
    expect(initialFields(acting).fallback_arguments).toBe(
      '{"set":{"status":"declined"}}',
    );
  });

  it("writes a fallback action's arguments as typed, and clears them when emptied", () => {
    const acting = typedActor({
      id: "ops",
      fallback: { kind: "action", value: "crm.approve", label: "x" },
      fallbackArguments: { set: { status: "declined" } },
    });
    expect(
      changesOf(acting, { fallback_arguments: '{"set": {"status": "ok"}}' }),
    ).toEqual({ fallback_arguments: { set: { status: "ok" } } });
    expect(changesOf(acting, { fallback_arguments: "" })).toEqual({
      fallback_arguments: null,
    });
  });

  it("sends a new actor only what was set, with the side it was added on", () => {
    expect(changesOf(NEW("caller"), {})).toEqual({ side: "caller" });
    expect(
      changesOf(
        NEW("world"),
        { name: "Sam", timing: "delayed", seconds: "5" },
      ),
    ).toEqual({
      side: "world",
      name: "Sam",
      response_timing: { kind: "delayed", seconds: 5 },
    });
  });
});

describe("input hints", () => {
  it("says when a record match is not yet a JSON object", () => {
    expect(whereProblem("")).toBe("");
    expect(whereProblem('{"status": "pending"}')).toBe("");
    expect(whereProblem("[1]")).toBe("an object of field values");
    expect(whereProblem('{"status": ')).toMatch(/such as/);
  });

  it("reads a fallback's arguments as a JSON object, empty as none, anything else as not one", () => {
    expect(parsedArguments("")).toEqual({});
    expect(parsedArguments('{"a": 1}')).toEqual({ a: 1 });
    expect(parsedArguments("[1]")).toBeNull();
    expect(parsedArguments("{")).toBeNull();
  });
});

describe("withStart", () => {
  it("keeps a stored value the options don't list, so a select never goes blank on it", () => {
    const options = [{ value: "person", label: "A person" }];
    expect(withStart(options, "person")).toBe(options);
    expect(withStart(options, "robot", "Robot")).toEqual([
      ...options,
      { value: "robot", label: "Robot" },
    ]);
    expect(withStart(options, "")).toBe(options);
  });
});
