import { describe, expect, it } from "vitest";
import {
  KEPT_TRIGGER,
  hasEdits,
  hidden,
  idFrom,
  idRule,
  readable,
  seenValue,
  toRecord,
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
    expect(toRecord(actor, { tool: KEPT_TRIGGER }, CONTEXT)).toEqual(actor);
    expect(
      toRecord(actor, { tool: "crm.note" }, CONTEXT).activation.trigger,
    ).toEqual({ kind: "on_tool", tool: "crm.note" });
    expect(toRecord(actor, { tool: "" }, CONTEXT).activation.trigger).toEqual({
      kind: "always",
    });
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

  it("changes a sub-actor's voice and loudness only when they are edited", () => {
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
    expect(
      toRecord({ channel: "audio_mix" }, { name: "Sam", id: "x" }, CONTEXT),
    ).toEqual({
      id: "x",
      name: "Sam",
      role: "",
      side: "caller",
      channel: "audio_mix",
      persona: {},
      loudness_ratio: null,
    });
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
      hidden([
        "scenario.sub_goals",
        "file.sub_goals.json",
        "file.runtime-validation.json",
        "colleague.goal",
        "colleague.prompt",
        "boss.goal",
        "boss.prompt",
      ]),
    ).toEqual({
      grader: ["sub goals", "runtime validation"],
      others: [["goal, prompt", 2]],
    });
    expect(hidden(undefined)).toEqual({ grader: [], others: [] });
  });
});
