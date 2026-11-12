// W4-1: the @ picker and the comment body format.
import { describe, expect, it } from "vitest";
import {
  activeMention,
  displayOfBody,
  insertMention,
  insertMentionName,
  mentionCandidates,
  noAccessNote,
  storedOfDisplay,
} from "../mentionModel";
import {
  anchorLabel,
  bodyParts,
  charCounter,
  commentActions,
  fieldAnchorOptions,
  filterThreads,
  foldLine,
  markdownOf,
  mentionIdsOf,
  mentionToken,
  mentionsText,
  noOpenText,
  openCommentsText,
  plainTextOf,
  reactionList,
  resolvedLine,
  stepAnchorOptions,
  traceSteps,
} from "../commentModel";

const meera = {
  id: "u-meera",
  name: "Meera Iyer",
  email: "meera.iyer@acme.ai",
};
const alice = {
  id: "u-alice",
  name: "Alice Park",
  email: "alice.park@acme.ai",
};
const asha = { id: "u-asha", name: "Asha Rao", email: "asha.rao@acme.ai" };

describe("mentionModel", () => {
  it("finds the @query at the caret, only at a word start", () => {
    expect(activeMention("Hi @Mee", 7)).toEqual({ query: "Mee", start: 3 });
    expect(activeMention("@", 1)).toEqual({ query: "", start: 0 });
    expect(activeMention("mail me@acme", 12)).toBeNull();
    expect(activeMention("Hi @Meera Iyer", 14)).toBeNull();
    expect(activeMention("(@as", 4)).toEqual({ query: "as", start: 1 });
  });

  it("lists members by name or email, people who can view first", () => {
    const can = (id) => id !== alice.id;
    expect(
      mentionCandidates([alice, meera, asha], "", can).map((m) => [
        m.id,
        m.noAccess,
      ]),
    ).toEqual([
      [meera.id, false],
      [asha.id, false],
      [alice.id, true],
    ]);
    expect(
      mentionCandidates([alice, meera], "iyer", can).map((m) => m.id),
    ).toEqual([meera.id]);
    expect(
      mentionCandidates([alice, meera], "ALICE.park", can).map((m) => m.id),
    ).toEqual([alice.id]);
  });

  it("puts the token in place of the @query", () => {
    const text = "Is this right @Mee";
    const out = insertMention(text, activeMention(text, text.length), meera);
    expect(out.text).toBe("Is this right @[Meera Iyer](user:u-meera) ");
    expect(out.caret).toBe(out.text.length);
    expect(noAccessNote("Alice Park")).toBe(
      "Alice Park can't view this dataset. An admin can give access in Dataset settings › Access.",
    );
  });
});

describe("the composer shows names and stores tokens", () => {
  it("writes a picked person as @Name and the token on the way out", () => {
    const text = "Is this right @Mee";
    const out = insertMentionName(
      text,
      activeMention(text, text.length),
      meera,
    );
    expect(out.text).toBe("Is this right @Meera Iyer ");
    expect(out.caret).toBe(out.text.length);
    expect(storedOfDisplay(`${out.text}and @Alice Park?`, [meera, alice])).toBe(
      "Is this right @[Meera Iyer](user:u-meera) and @[Alice Park](user:u-alice)?",
    );
  });

  it("only turns picked names at a word start into tokens", () => {
    const meeraShort = { id: "u-m2", name: "Meera" };
    // The longer name wins; a name glued to more letters stays text.
    expect(
      storedOfDisplay("@Meera Iyer and @Meera, not @MeeraX or a@Meera", [
        meeraShort,
        meera,
      ]),
    ).toBe(
      "@[Meera Iyer](user:u-meera) and @[Meera](user:u-m2), not @MeeraX or a@Meera",
    );
    // A name deleted from the text is no longer mentioned.
    expect(mentionIdsOf(storedOfDisplay("no one here", [meera]))).toEqual([]);
    expect(storedOfDisplay("(@Asha Rao)", [asha])).toBe(
      "(@[Asha Rao](user:u-asha))",
    );
  });

  it("round-trips a stored body for editing", () => {
    const stored =
      "@[Meera Iyer](user:u-meera) check **this** with @[Asha Rao](user:u-asha)";
    const shown = displayOfBody(stored);
    expect(shown.text).toBe("@Meera Iyer check **this** with @Asha Rao");
    expect(shown.mentions).toEqual([
      { id: "u-meera", name: "Meera Iyer" },
      { id: "u-asha", name: "Asha Rao" },
    ]);
    expect(storedOfDisplay(shown.text, shown.mentions)).toBe(stored);
  });
});

describe("commentModel", () => {
  const body = `${mentionToken(meera)} and ${mentionToken(asha)} and ${mentionToken(meera)} **check**`;

  it("reads mentions from ids, renders names", () => {
    expect(mentionIdsOf(body)).toEqual([meera.id, asha.id]);
    expect(plainTextOf(body)).toBe(
      "@Meera Iyer and @Asha Rao and @Meera Iyer **check**",
    );
    expect(markdownOf(body)).toContain("**@Meera Iyer**");
    expect(bodyParts(`hi ${mentionToken(meera)}!`)).toEqual([
      { kind: "text", text: "hi " },
      { kind: "mention", id: meera.id, name: meera.name },
      { kind: "text", text: "!" },
    ]);
    // A name alone is not a mention.
    expect(mentionIdsOf("@Meera Iyer please")).toEqual([]);
  });

  it("labels anchors", () => {
    expect(anchorLabel({ kind: "row" })).toBe("");
    expect(anchorLabel({ kind: "field", column_name: "expected_reply" })).toBe(
      "on expected_reply",
    );
    expect(
      anchorLabel(
        { kind: "step", step_label: "3 lookup_order" },
        { runLabel: "#4A" },
      ),
    ).toBe("on #4A · step 3 lookup_order");
    expect(anchorLabel({ kind: "row" }, { runLabel: "#4A" })).toBe("on #4A");
  });

  it("counts characters left from the server's limits", () => {
    const limits = { body_max: 2000, warn_from: 1800 };
    expect(charCounter("x".repeat(1799), limits)).toBeNull();
    expect(charCounter("x".repeat(1880), limits).text).toBe(
      "120 characters left",
    );
    expect(charCounter("x".repeat(1999), limits).text).toBe("1 character left");
    expect(charCounter("x".repeat(2003), limits)).toMatchObject({
      over: true,
      text: "3 over the limit",
    });
    expect(charCounter("x", null)).toBeNull();
  });

  it("decides actions like the server", () => {
    const root = { id: "c1", author: { id: "u1" }, parent_id: null };
    expect(commentActions(root, { meId: "u1" })).toEqual({
      reply: true,
      react: true,
      resolve: true,
      edit: true,
      remove: true,
    });
    expect(commentActions(root, { meId: "u2" })).toEqual({
      reply: true,
      react: true,
      resolve: false,
      edit: false,
      remove: false,
    });
    expect(
      commentActions(root, { meId: "u2", mentionedInThread: true }).resolve,
    ).toBe(true);
    expect(commentActions(root, { meId: "u2", canEdit: true }).resolve).toBe(
      true,
    );
    expect(commentActions(root, { meId: "u2", isAdmin: true }).remove).toBe(
      true,
    );
    const reply = { ...root, parent_id: "c0" };
    expect(commentActions(reply, { meId: "u1" })).toMatchObject({
      reply: false,
      resolve: false,
    });
    expect(
      commentActions({ ...root, deleted: true }, { meId: "u1" }).edit,
    ).toBe(false);
  });

  it("lists reactions in the server's order with who reacted", () => {
    const list = reactionList({ "👍": ["u1", "u2"] }, ["👍", "✅", "👀"], {
      meId: "u1",
      nameOf: (id) => (id === "u2" ? "Asha Rao" : null),
    });
    expect(list).toEqual([
      { emoji: "👍", count: 2, mine: true, names: ["You", "Asha Rao"] },
      { emoji: "✅", count: 0, mine: false, names: [] },
      { emoji: "👀", count: 0, mine: false, names: [] },
    ]);
  });

  it("filters threads and writes the counts", () => {
    const threads = [
      { root: { resolved: false } },
      { root: { resolved: true, resolved_by: { id: "u2", name: "Asha Rao" } } },
      { root: { deleted: true } },
    ];
    expect(filterThreads(threads, "open")).toHaveLength(1);
    expect(filterThreads(threads, "resolved")).toHaveLength(1);
    expect(filterThreads(threads, "all")).toHaveLength(3);
    expect(resolvedLine(threads[1].root, "u1")).toBe("Resolved by Asha Rao");
    expect(resolvedLine(threads[1].root, "u2")).toBe("Resolved by you");
    // The fold names whose comment it was and what it said.
    const folded = {
      resolved: true,
      resolved_by: { id: "u2", name: "Maya Chen" },
      author: { id: "u2", name: "Maya Chen" },
      body: "@[Meera Iyer](user:u-meera) Is this the **right** window?",
    };
    expect(foldLine(folded, "u2", "on expected_reply")).toBe(
      "Resolved by you · your comment on expected_reply: “@Meera Iyer Is this the right window?”",
    );
    expect(
      foldLine({ ...folded, author: { id: "u9", name: "Asha Rao" } }, "u3"),
    ).toBe(
      "Resolved by Maya Chen · Asha Rao's comment: “@Meera Iyer Is this the right window?”",
    );
    expect(foldLine({ ...folded, deleted: true, body: "" }, "u2")).toBe(
      "Resolved by you · your comment",
    );
    expect(foldLine({ ...folded, resolved: false }, "u2")).toBe("");
    expect(openCommentsText(2)).toBe("2 open comments");
    expect(openCommentsText(1)).toBe("1 open comment");
    expect(openCommentsText(0)).toBe("");
    expect(mentionsText(1)).toBe("1 mention of you");
    expect(mentionsText(3)).toBe("3 mentions of you");
  });

  it("offers the row's user fields and the try's steps as anchors", () => {
    const opts = fieldAnchorOptions([
      { id: "c1", name: "customer_message", role: "input" },
      {
        id: "c2",
        name: "Draft reply",
        role: "output",
        origin_type: "run_prompt",
      },
      {
        id: "c3",
        name: "Correctness",
        role: "eval",
        origin_type: "evaluation",
      },
      { id: "c4", name: "intent", role: "metadata" },
    ]);
    expect(opts.map((o) => o.label)).toEqual([
      "Whole row",
      "customer_message",
      "intent",
    ]);
    expect(opts[1].anchor).toEqual({
      kind: "field",
      column_id: "c1",
      column_name: "customer_message",
    });
    const steps = traceSteps([
      {
        observation_span: { id: "root", name: "Support Agent" },
        children: [
          { observation_span: { id: "s1", name: "plan" }, children: [] },
          {
            observation_span: { id: "s2", name: "lookup_order" },
            children: [
              { observation_span: { id: "s3", name: "db" }, children: [] },
            ],
          },
        ],
      },
    ]);
    expect(steps.map((s) => s.span_id)).toEqual(["s1", "s2", "s3"]);
    const so = stepAnchorOptions(steps, "t1");
    expect(so[1]).toEqual({
      key: "step:s2",
      label: "2 lookup_order",
      anchor: {
        kind: "step",
        trace_id: "t1",
        span_id: "s2",
        step_label: "2 lookup_order",
      },
    });
    expect(
      traceSteps([
        { observation_span: { id: "only", name: "llm" }, children: [] },
      ]).map((s) => s.span_id),
    ).toEqual(["only"]);
  });
});

describe("commentModel: the Open view when nothing is open", () => {
  it("tells resolved threads from deleted roots that still have replies", () => {
    expect(noOpenText([])).toBeNull();
    expect(noOpenText([{ root: { resolved: true } }])).toBe(
      "Every thread here is resolved.",
    );
    expect(
      noOpenText([{ root: { resolved: true } }, { root: { deleted: true } }]),
    ).toBe("No open threads. All shows the rest.");
  });
});

describe("footer counts name their rows when they differ", () => {
  it("open comments and mentions", () => {
    expect(openCommentsText(3, 2)).toBe("3 open comments on 2 rows");
    expect(openCommentsText(2, 2)).toBe("2 open comments");
    expect(openCommentsText(2, 1)).toBe("2 open comments on 1 row");
    expect(openCommentsText(0, 0)).toBe("");
    expect(mentionsText(2, 1)).toBe("2 mentions of you on 1 row");
    expect(mentionsText(1, 1)).toBe("1 mention of you");
  });
});
