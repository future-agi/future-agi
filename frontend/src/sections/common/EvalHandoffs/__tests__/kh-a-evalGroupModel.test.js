import { describe, expect, it } from "vitest";
import {
  addedGroupText,
  andList,
  groupLine,
  memberPreview,
  membersToAdd,
} from "../evalGroupModel";

const SE = {
  eval_group: { name: "Support essentials" },
  members: [
    { eval_template_id: "c", name: "correctness_vs_reference" },
    { eval_template_id: "g", name: "groundedness_policy_kb" },
    { eval_template_id: "t", name: "tone_on_brand" },
    { eval_template_id: "k", name: "task_completion" },
  ],
};

describe("evalGroupModel", () => {
  it("adds the members the step lacks, by template id", () => {
    const existing = [
      { templateId: "c", name: "Correctness" },
      { template_id: "g", name: "Groundedness" },
      { evalId: "t", name: "Tone" },
      { templateId: "x", name: "Other" },
    ];
    const { add, skipped } = membersToAdd(SE, existing);
    expect(add.map((m) => m.eval_template_id)).toEqual(["k"]);
    expect(skipped.map((s) => s.entry.name)).toEqual([
      "Correctness",
      "Groundedness",
      "Tone",
    ]);
    expect(membersToAdd(SE, []).add).toHaveLength(4);
    expect(membersToAdd(null, existing)).toEqual({
      add: [],
      skipped: [],
      refused: [],
    });
  });

  it("keeps the members the group read leaves out apart from those it adds", () => {
    const read = {
      eval_group: { name: "Checks" },
      members: [
        { eval_template_id: "c", name: "correctness_vs_reference" },
        {
          eval_template_id: "s",
          name: "safety",
          apply_refusal:
            "Won't be added: it uses Turing Large, which can't run here.",
          apply_refusal_code: "built_in",
        },
        {
          eval_template_id: "b",
          name: "bias",
          apply_refusal:
            "Won't be added: its model, jev-latest, can't run on this deployment. Change the evaluation's model, then apply again.",
          apply_refusal_code: "model",
        },
      ],
    };
    // A member already on the step stays already added.
    const { add, skipped, refused } = membersToAdd(read, [
      { templateId: "b", name: "Bias" },
    ]);
    expect(add.map((m) => m.eval_template_id)).toEqual(["c"]);
    expect(refused.map((m) => m.eval_template_id)).toEqual(["s"]);
    expect(skipped.map((s) => s.entry.name)).toEqual(["Bias"]);
    expect(membersToAdd(read, []).refused.map((m) => m.name)).toEqual([
      "safety",
      "bias",
    ]);
  });

  it("names a group and previews its members", () => {
    expect(groupLine(SE)).toBe("Support essentials · 4 evaluations");
    expect(groupLine({ name: "One", evals_count: 1 })).toBe(
      "One · 1 evaluation",
    );
    expect(memberPreview(SE.members)).toBe(
      "correctness_vs_reference, groundedness_policy_kb, tone_on_brand +1",
    );
    expect(memberPreview(SE.members.slice(0, 2))).toBe(
      "correctness_vs_reference, groundedness_policy_kb",
    );
  });

  it("says what was added", () => {
    expect(andList(["A"])).toBe("A");
    expect(andList(["A", "B", "C"])).toBe("A, B and C");
    expect(
      addedGroupText("Support essentials", {
        added: 1,
        skippedNames: ["Correctness", "Groundedness", "Tone"],
      }),
    ).toBe(
      "Added 1 evaluation from Support essentials · Correctness, Groundedness and Tone were already added.",
    );
    expect(addedGroupText("G", { added: 2, skippedNames: ["Tone"] })).toBe(
      "Added 2 evaluations from G · Tone was already added.",
    );
    expect(addedGroupText("G", { added: 3, skippedNames: [] })).toBe(
      "Added 3 evaluations from G.",
    );
    expect(
      addedGroupText("Support essentials", { added: 0, skippedNames: ["A"] }),
    ).toBe("Every evaluation in Support essentials is already added.");
  });

  it("counts only the members added when the read leaves some out", () => {
    expect(
      addedGroupText("G", { added: 2, skippedNames: [], refused: 1 }),
    ).toBe("Added 2 evaluations from G.");
    // Nothing added: the lines for the members left out say why, and the
    // group is never called already added.
    expect(
      addedGroupText("G", { added: 0, skippedNames: [], refused: 2 }),
    ).toBeNull();
    expect(
      addedGroupText("G", { added: 0, skippedNames: ["A"], refused: 1 }),
    ).toBeNull();
  });
});
