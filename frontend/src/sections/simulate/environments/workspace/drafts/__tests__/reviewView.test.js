import { describe, it, expect } from "vitest";
import {
  diffLines,
  groupChanges,
  keptChanges,
  recheckedKeys,
  rejectBlock,
  restoreBlock,
  reviewCounts,
  saveLine,
  sourceLine,
  sourceLink,
} from "../reviewView";

const change = (overrides) => ({
  id: "tool:refund",
  piece: "environment",
  kind: "tool",
  key: "refund",
  action: "changed",
  patch: [],
  summary: "Change limit of tool refund",
  decision: null,
  depends_on: [],
  graded: true,
  revalidate: [],
  ...overrides,
});

describe("diffLines", () => {
  it("keeps a reworded rule's old text above the new", () => {
    expect(
      diffLines(
        change({
          kind: "rule",
          patch: [
            { op: "test", path: "", value: "R2: old wording" },
            { op: "replace", path: "", value: "R2: new wording" },
          ],
        }),
      ),
    ).toEqual([
      {
        sign: "~",
        field: "Rules",
        before: "R2: old wording",
        text: "R2: new wording",
      },
    ]);
  });

  it("reads a changed field as old → new, named after its item", () => {
    expect(
      diffLines(
        change({
          key: "lookup_booking",
          patch: [
            { op: "test", path: "/answers_with", value: "Recorded" },
            { op: "replace", path: "/answers_with", value: "Simulated" },
          ],
        }),
      ),
    ).toEqual([
      {
        sign: "~",
        field: "Tools",
        text: "lookup_booking · answers with: Recorded → Simulated",
      },
    ]);
  });

  it("signs a whole item added or removed, by its name", () => {
    expect(
      diffLines(
        change({
          kind: "actor",
          action: "added",
          patch: [{ op: "add", path: "", value: { id: "a1", name: "Boss" } }],
        }),
      ),
    ).toEqual([{ sign: "+", field: "Actors", text: "Boss" }]);
    expect(
      diffLines(
        change({
          kind: "rule",
          action: "removed",
          patch: [{ op: "test", path: "", value: "Old rule" }],
        }),
      ),
    ).toEqual([{ sign: "−", field: "Rules", text: "Old rule" }]);
  });

  it("reads world rows as a table's counts", () => {
    expect(
      diffLines(
        change({
          kind: "world_state",
          rows: [{ table: "orders", added: 12, removed: 0, changed: 3 }],
        }),
      ),
    ).toEqual([
      { sign: "~", field: "Tables", text: "orders · 12 added, 3 changed" },
    ]);
  });

  it("has no lines for a change with nothing to show", () => {
    expect(diffLines(change({ kind: "file" }))).toEqual([]);
    expect(diffLines(change({ kind: "world_state", rows: null }))).toEqual([]);
  });
});

describe("groupChanges", () => {
  it("keeps the design's order and leaves out empty groups", () => {
    const groups = groupChanges([
      change({ id: "scenario:a", kind: "scenario" }),
      change({ id: "world_state:b", kind: "world_state" }),
      change({ id: "rule:c", kind: "rule" }),
    ]);
    expect(groups.map((group) => group.id)).toEqual([
      "rules",
      "state",
      "scenarios",
    ]);
  });
});

describe("kept changes", () => {
  it("drops undone changes from the list and the recheck", () => {
    const changes = [
      change({ id: "a", revalidate: ["s1", "s2"] }),
      change({ id: "b", revalidate: ["s2", "s3"], decision: "rejected" }),
    ];
    expect(keptChanges(changes).map((one) => one.id)).toEqual(["a"]);
    expect(recheckedKeys(changes)).toEqual(["s1", "s2"]);
  });
});

describe("reject and restore order", () => {
  const actor = change({
    id: "actor:boss",
    kind: "actor",
    summary: "Add boss",
  });
  const scenario = change({
    id: "scenario:s",
    kind: "scenario",
    summary: "Change tests of s",
    depends_on: ["actor:boss"],
  });

  it("holds Reject while a kept change needs this one", () => {
    expect(rejectBlock(actor, [actor, scenario])).toEqual({
      by: "Change tests of s",
    });
    expect(
      rejectBlock(actor, [actor, { ...scenario, decision: "rejected" }]),
    ).toBeNull();
    expect(rejectBlock(scenario, [actor, scenario])).toBeNull();
  });

  it("holds Restore while something this one needs is rejected", () => {
    const rejectedActor = { ...actor, decision: "rejected" };
    const rejectedScenario = { ...scenario, decision: "rejected" };
    expect(
      restoreBlock(rejectedScenario, [rejectedActor, rejectedScenario]),
    ).toEqual({ by: "Add boss" });
    expect(
      restoreBlock(rejectedActor, [rejectedActor, rejectedScenario]),
    ).toBeNull();
    expect(
      restoreBlock(rejectedScenario, [actor, rejectedScenario]),
    ).toBeNull();
  });
});

describe("reviewCounts", () => {
  it("counts each decision and what's still waiting", () => {
    expect(
      reviewCounts([
        change({ decision: "accepted" }),
        change({ decision: "rejected" }),
        change(),
      ]),
    ).toEqual({ total: 3, accepted: 1, rejected: 1, undecided: 1 });
  });
});

describe("sources", () => {
  it("tells a chat change, a file row and a direct edit apart", () => {
    const chat = { origin: "chat", used: [{ kind: "message", id: "m1" }] };
    const file = {
      origin: "manual",
      used: [{ kind: "file", id: "f-1", name: "o.csv", row: 7 }],
    };
    expect(sourceLine(chat).kind).toBe("chat");
    expect(sourceLine(file)).toMatchObject({
      kind: "file",
      file: "o.csv",
      row: 7,
    });
    expect(sourceLine({ origin: "manual" }).kind).toBe("hand");
    expect(sourceLink(chat)).toMatchObject({ kind: "chat" });
    // The message it answered, as the server names it.
    expect(
      sourceLink({ origin: "chat", used: [{ kind: "message", id: "m2" }] }),
    ).toEqual({ kind: "chat", messageId: "m2" });
    expect(sourceLink(file)).toEqual({ kind: "file", file: "o.csv", row: 7 });
    expect(sourceLink({ origin: "manual" }).kind).toBe("tab");
  });

  it("reads a file with no name, and a whole file, without showing its id", () => {
    const unnamed = {
      origin: "manual",
      used: [{ kind: "file", id: "f-1", row: null }],
    };
    expect(sourceLine(unnamed)).toMatchObject({
      kind: "file",
      file: null,
      row: null,
    });
    expect(sourceLink(unnamed)).toEqual({
      kind: "file",
      file: null,
      row: null,
    });
  });

  it("names the file a chat change read and still links to its message", () => {
    const both = {
      origin: "chat",
      used: [
        { kind: "message", id: "m2" },
        { kind: "file", id: "f-1", name: "o.csv", row: 7 },
      ],
    };
    expect(sourceLine(both)).toMatchObject({ kind: "file", file: "o.csv" });
    expect(sourceLink(both)).toEqual({ kind: "chat", messageId: "m2" });
  });
});

describe("saveLine", () => {
  it("names the edit and the recheck count", () => {
    expect(
      saveLine([change({ kind: "rule", revalidate: ["s1", "s2"] })]),
    ).toEqual({ kind: "spec", rechecked: 2 });
  });

  it("is a scenario edit when only scenarios changed", () => {
    expect(saveLine([change({ piece: "suite", kind: "scenario" })]).kind).toBe(
      "scenarios",
    );
  });
});
