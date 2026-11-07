import { describe, expect, it } from "vitest";
import {
  KEPT_TRIGGER,
  capProblem,
  hasEdits,
  hidden,
  idFrom,
  idRule,
  parsedArguments,
  readable,
  seenValue,
  toRecord,
  triggerProblem,
} from "../actorUtils";

const CONTEXT = { others: [], loudness: { channels: ["audio_mix"] } };

describe("toRecord", () => {
  const BUILDER_WRITTEN = {
    id: "manager",
    name: "Priya",
    permitted_actions: ["crm.approve"],
    activation: null,
    behaviour: { response_timing: { kind: "immediate", seconds: 7 } },
    knowledge: [
      { kind: "facts" },
      { kind: "facts", text: "a\nb" },
      { kind: "records", path: "crm.json" },
      { kind: "facts", text: "c", source: "builder" },
    ],
  };

  it("sends an existing actor back unchanged when nothing was edited", () => {
    expect(toRecord(BUILDER_WRITTEN, {}, CONTEXT)).toEqual(BUILDER_WRITTEN);
  });

  it("ignores a field that went back to its start value, and says there are no edits", () => {
    const fields = {
      name: "Priya ",
      goal: "",
      fallback: "silent",
      tool: "",
      facts: "a\nb\nc",
    };
    expect(hasEdits(BUILDER_WRITTEN, fields)).toBe(false);
    expect(toRecord(BUILDER_WRITTEN, fields, CONTEXT)).toEqual(BUILDER_WRITTEN);
    expect(hasEdits(BUILDER_WRITTEN, { goal: "Approve" })).toBe(true);
  });

  it("overlays only the edited field, inside its own block", () => {
    const actor = {
      ...BUILDER_WRITTEN,
      constraints: { may_agree: ["x"], extra: true },
      activation: {
        trigger: { kind: "immediate_unknown", at: 3 },
        timeout_seconds: 9,
      },
    };
    const edited = toRecord(
      actor,
      { stop_when: "Done\n\n", timeout: "12", style: "brisk" },
      CONTEXT,
    );
    expect(edited).toEqual({
      ...actor,
      constraints: { may_agree: ["x"], extra: true, stop_when: ["Done"] },
      activation: { ...actor.activation, timeout_seconds: 12 },
      behaviour: { ...actor.behaviour, style: "brisk" },
    });
  });

  it("keeps a trigger of any kind until Comes in really changes", () => {
    const trigger = { kind: "after_seconds", seconds: 30 };
    const actor = { id: "a", name: "A", activation: { trigger } };
    expect(toRecord(actor, { mode: "after_seconds" }, CONTEXT)).toEqual(actor);
    expect(
      toRecord(actor, { mode: "on_tool", triggerTool: "crm.note" }, CONTEXT)
        .activation.trigger,
    ).toEqual({ kind: "on_tool", tool: "crm.note" });
    expect(
      toRecord(actor, { mode: "always" }, CONTEXT).activation.trigger,
    ).toEqual({
      kind: "always",
    });
    // A trigger the form cannot make itself (a later call of a tool) is kept as it is.
    const later = {
      id: "a",
      name: "A",
      activation: { trigger: { kind: "on_tool", tool: "x", on_call: 2 } },
    };
    expect(
      toRecord(later, { mode: KEPT_TRIGGER, name: "B" }, CONTEXT).activation,
    ).toEqual(later.activation);
  });

  it("writes each of the five ways an actor comes in, with its own fields", () => {
    const actor = { id: "a", name: "A" };
    const trigger = (fields) =>
      toRecord(actor, fields, CONTEXT).activation.trigger;
    expect(trigger({ mode: "contacted" })).toEqual({ kind: "contacted" });
    expect(trigger({ mode: "after_seconds", triggerSeconds: "45" })).toEqual({
      kind: "after_seconds",
      seconds: 45,
    });
    expect(
      trigger({ mode: "on_keyword", triggerWords: "refund, manager" }),
    ).toEqual({
      kind: "on_keyword",
      words: ["refund", "manager"],
    });
    expect(trigger({ mode: "mid_journey", triggerTurn: "3" })).toEqual({
      kind: "mid_journey",
      turn: 3,
    });
    expect(
      trigger({
        mode: "conditional",
        triggerCollection: "approvals",
        triggerWhere: '{"status": "pending"}',
      }),
    ).toEqual({
      kind: "conditional",
      collection: "approvals",
      where: { status: "pending" },
    });
  });

  it("writes an actor's own model and caps, and empty takes them away", () => {
    const actor = {
      id: "a",
      name: "A",
      model: "gemini-3.7-flash",
      max_turns: 4,
    };
    const record = toRecord(actor, { model: "", max_turns: "" }, CONTEXT);
    expect(record.model).toBeUndefined();
    expect(record.max_turns).toBeUndefined();
    expect(
      toRecord({ id: "b", name: "B" }, { max_lines: "3" }, CONTEXT).max_lines,
    ).toBe(3);
  });

  it("rewrites only the facts: other knowledge keeps its place, an unchanged fact keeps its keys", () => {
    const { knowledge } = toRecord(
      BUILDER_WRITTEN,
      { facts: "new\nc\na\nb\nanother" },
      CONTEXT,
    );
    expect(knowledge).toEqual([
      { kind: "facts" },
      { kind: "facts", text: "new" },
      { kind: "records", path: "crm.json" },
      { kind: "facts", text: "c", source: "builder" },
      { kind: "facts", text: "a\nb" },
      { kind: "facts", text: "another" },
    ]);
  });

  it("drops a fact whose line was deleted, and nothing else", () => {
    const { knowledge } = toRecord(BUILDER_WRITTEN, { facts: "c" }, CONTEXT);
    expect(knowledge).toEqual([
      { kind: "facts" },
      { kind: "facts", text: "c", source: "builder" },
      { kind: "records", path: "crm.json" },
    ]);
  });

  it("trims a name and role, and keeps a set persona name in step with the name", () => {
    const actor = {
      id: "jordan",
      name: "Jordan",
      role: "son",
      persona: { name: "Jordan", gender: "male" },
    };
    expect(
      toRecord(actor, { name: " Jordy ", role: " brother " }, CONTEXT),
    ).toEqual({
      ...actor,
      name: "Jordy",
      role: "brother",
      persona: { name: "Jordy", gender: "male" },
    });
    expect(toRecord(actor, { role: "brother" }, CONTEXT).persona).toBe(
      actor.persona,
    );
    expect(
      toRecord({ id: "a", name: "A" }, { name: "B" }, CONTEXT),
    ).not.toHaveProperty("persona");
    expect(
      toRecord(
        { id: "a", name: "A", persona: { name: "", gender: "male" } },
        { name: "B" },
        CONTEXT,
      ).persona,
    ).toEqual({ name: "", gender: "male" });
  });

  it("changes an actor's voice and loudness only when they are edited", () => {
    const actor = {
      id: "colleague",
      name: "Jordan",
      side: "caller",
      channel: "audio_mix",
      loudness_ratio: 0.5,
      persona: { gender: "male", occupation: "Engineer" },
    };
    expect(
      toRecord(actor, { gender: "", accent: "British" }, CONTEXT).persona,
    ).toEqual({ occupation: "Engineer", accent: "British" });
    expect(
      toRecord(actor, { channel: "ivr" }, CONTEXT).loudness_ratio,
    ).toBeNull();
    const handed = { ...actor, channel: "audio_handoff" };
    expect(toRecord(handed, { name: "J" }, CONTEXT).loudness_ratio).toBe(0.5);
  });

  it("sends a new actor everything a create carries, with the id made from its name", () => {
    expect(toRecord({}, { name: " Sam Lee " }, CONTEXT)).toEqual({
      id: "sam_lee",
      name: "Sam Lee",
      role: "",
      kind: "person",
      parent: null,
      goal: "",
      constraints: {
        may_agree: [],
        private: [],
        escalate_when: [],
        stop_when: [],
      },
      knowledge: [],
      behaviour: {
        style: "",
        response_timing: { kind: "immediate", seconds: 0 },
      },
      permitted_actions: [],
      activation: {
        trigger: { kind: "always" },
        timeout_seconds: 0,
        fallback: "silent",
      },
    });
    // An actor who speaks gets every field too, with how it joins; no side, since there is no split.
    const speaking = toRecord(
      { channel: "audio_mix" },
      { name: "Sam", id: "x", gender: "female" },
      CONTEXT,
    );
    expect(speaking).toMatchObject({
      id: "x",
      name: "Sam",
      channel: "audio_mix",
      persona: { gender: "female" },
      loudness_ratio: null,
      permitted_actions: [],
      goal: "",
    });
    expect(speaking).not.toHaveProperty("side");
  });
});

describe("actorUtils", () => {
  it("makes an id from a name, folding accents and staying unique in the cast", () => {
    expect(idFrom("Sam Lee", new Set())).toBe("sam_lee");
    expect(idFrom("Sam Lee", new Set(["sam_lee", "sam_lee_2"]))).toBe(
      "sam_lee_3",
    );
    expect(idFrom("Zoë  O'Brien", new Set())).toBe("zoe_o_brien");
    expect(idFrom("", new Set())).toBe("actor");
    expect(idFrom("!!!", new Set())).toBe("actor");
    expect(idFrom("x".repeat(80), new Set())).toHaveLength(60);
  });

  it("reads the id rule from the record schemas, and none when they send none", () => {
    const rule = idRule({
      "actors.json": { properties: { id: { pattern: "^[a-z_]{1,4}$" } } },
    });
    expect([rule.test("ab_c"), rule.test("abcde"), rule.test("A")]).toEqual([
      true,
      false,
      false,
    ]);
    expect(idRule({})).toBeNull();
    expect(idRule(undefined)).toBeNull();
  });

  it("reads a view value as words, never raw JSON", () => {
    expect(readable(null)).toBe("");
    expect(readable(0)).toBe("0");
    expect(readable(["a", "", "b"])).toBe("a; b");
    expect(readable({ kind: "facts", text: "The limit is 25%" })).toBe(
      "The limit is 25%",
    );
    expect(
      readable({
        kind: "records",
        response_timing: { kind: "delayed", seconds: 5 },
      }),
    ).toBe("records · response timing: delayed · seconds: 5");
  });

  it("reads a persona as its values beside its own label, any other field keeping its name", () => {
    expect(
      seenValue("actor.persona", {
        gender: "female",
        age_group: "32-49",
        occupation: "Engineer",
      }),
    ).toBe("Female · 32-49 · occupation: Engineer");
    expect(seenValue("actor.goal", "Approve")).toBe("Approve");
  });

  it("groups what an actor never sees into grader material and other actors' fields", () => {
    expect(
      hidden({
        scenario_fields: ["sub_goals"],
        files: ["sub_goals.json", "runtime-validation.json"],
        // Named by name, and a name may hold a dot or the word file.
        other_actors: [
          "Dr. File.goal",
          "Dr. File.prompt",
          "boss.goal",
          "boss.prompt",
        ],
      }),
    ).toEqual({
      grader: ["sub goals", "runtime validation"],
      others: [["goal, prompt", 2]],
    });
    expect(hidden(undefined)).toEqual({ grader: [], others: [] });
  });
});

describe("toRecord play", () => {
  it("sends how an actor acts in the world only when it is changed", () => {
    const actor = { id: "m", name: "Mia", permitted_actions: [] };
    expect(toRecord(actor, { play: "live" }, {})).toMatchObject({
      play: "live",
    });
    expect(toRecord(actor, { play: "mocked" }, {})).not.toHaveProperty("play");
  });
});

describe("toRecord knowledge picks", () => {
  const sources = {
    files: ["docs/policy.md", "docs/faq.md"],
    records: [{ store: "crm", collection: "deals" }],
  };
  it("puts the picked files and records in place of the earlier ones and keeps facts and a record's where", () => {
    const actor = {
      id: "m",
      name: "Mia",
      knowledge: [
        { kind: "facts", text: "Limit is 15%" },
        { kind: "files", paths: ["docs/faq.md"] },
        {
          kind: "records",
          store: "crm",
          collection: "deals",
          where: { owner: "mia" },
        },
      ],
    };
    const record = toRecord(
      actor,
      { files: ["docs/policy.md"], recordsRead: ["crm.deals"] },
      { knowledgeSources: sources },
    );
    expect(record.knowledge).toEqual([
      { kind: "facts", text: "Limit is 15%" },
      { kind: "files", paths: ["docs/policy.md"] },
      {
        kind: "records",
        store: "crm",
        collection: "deals",
        where: { owner: "mia" },
      },
    ]);
  });

  it("leaves the knowledge as it was when nothing is picked anew", () => {
    const knowledge = [{ kind: "files", paths: ["docs/faq.md"] }];
    const record = toRecord(
      { id: "m", name: "Mia", knowledge },
      { files: ["docs/faq.md"] },
      { knowledgeSources: sources },
    );
    expect(record.knowledge).toBe(knowledge);
  });
});

describe("capProblem", () => {
  it.each([
    ["", null],
    ["  ", null],
    ["3", null],
    ["2.7", "A whole number above 0, or empty for no limit"],
    ["0", "A whole number above 0, or empty for no limit"],
    ["-1", "A whole number above 0, or empty for no limit"],
    ["e", "A whole number above 0, or empty for no limit"],
  ])("a typed cap of %j: %s", (typed, expected) => {
    expect(capProblem(typed)).toBe(expected);
  });
});

describe("triggerProblem", () => {
  const reader = (fields) => (name) => fields[name] ?? "";
  it.each([
    [
      { mode: "on_tool" },
      { triggerTool: "Pick the action that brings them in" },
    ],
    [{ mode: "on_tool", triggerTool: "crm.update" }, {}],
    [
      { mode: "on_keyword", triggerWords: " , " },
      { triggerWords: "Type at least one word" },
    ],
    [{ mode: "on_keyword", triggerWords: "refund" }, {}],
    [
      { mode: "conditional" },
      { triggerCollection: "Pick the records that bring them in" },
    ],
    [
      {
        mode: "conditional",
        triggerCollection: "approvals",
        triggerWhere: "{status: pending}",
      },
      {
        triggerWhere:
          'an object of field values, such as {"status": "pending"}',
      },
    ],
    [
      {
        mode: "conditional",
        triggerCollection: "approvals",
        triggerWhere: '{"status": "pending"}',
      },
      {},
    ],
    [{ mode: "always" }, {}],
  ])("says what %o still needs", (fields, expected) => {
    expect(triggerProblem(reader(fields))).toEqual(expected);
  });
});

describe("a fallback action's arguments", () => {
  const DANA = {
    id: "supervisor",
    name: "Dana Brooks",
    permitted_actions: ["approvals.change"],
    activation: {
      trigger: { kind: "conditional", collection: "approvals" },
      timeout_seconds: 30,
      fallback: "approvals.change",
      fallback_arguments: {
        where: { status: "pending" },
        set: { status: "declined" },
      },
    },
  };

  it("are kept when the form saves an edit elsewhere", () => {
    for (const fields of [{ role: "Claims supervisor" }, { timeout: 45 }]) {
      const saved = toRecord(DANA, fields, CONTEXT);
      expect(saved.activation.fallback_arguments).toEqual(
        DANA.activation.fallback_arguments,
      );
    }
  });

  it("are dropped when the fallback changes to something else", () => {
    const saved = toRecord(DANA, { fallback: "decline" }, CONTEXT);
    expect(saved.activation.fallback).toBe("decline");
    expect(saved.activation).not.toHaveProperty("fallback_arguments");
  });

  it("are written as typed, and cleared when emptied", () => {
    const typed = toRecord(
      DANA,
      {
        fallback_arguments:
          '{"where": {"id": 1}, "set": {"status": "declined"}}',
      },
      CONTEXT,
    );
    expect(typed.activation.fallback_arguments).toEqual({
      where: { id: 1 },
      set: { status: "declined" },
    });
    const emptied = toRecord(DANA, { fallback_arguments: "" }, CONTEXT);
    expect(emptied.activation).not.toHaveProperty("fallback_arguments");
  });

  it("must be a JSON object", () => {
    expect(parsedArguments("")).toEqual({});
    expect(parsedArguments("[1]")).toBeNull();
    expect(parsedArguments("{oops")).toBeNull();
  });
});
