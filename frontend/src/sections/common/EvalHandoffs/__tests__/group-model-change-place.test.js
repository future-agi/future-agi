// A member left out of a group apply says where its model can be changed,
// and that place has the control. The model is the one the request sends,
// else the evaluation's own (or Add evaluation's default when it names none):
// - "Change the group's model": the Evaluations drawer, where a group is
//   applied, has a Language Model field, and its group requests send it;
// - "Change the evaluation's model": the evaluation's own page edits its
//   model with the model selector.
// Both screens are too wide to mount here, so their source is read.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SECTIONS = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
);
const source = (...parts) => readFileSync(join(SECTIONS, ...parts), "utf8");

describe("the place a refused member's model is changed", () => {
  it("the group's model: the drawer applying a group has the model field", () => {
    const content = source(
      "common",
      "EvaluationDrawer",
      "EvaluationMappingFormContent.jsx",
    );
    const form = source(
      "common",
      "EvaluationDrawer",
      "EvaluationMappingForm.jsx",
    );

    // The field shows for a group with Future AGI evaluations, as for one
    // evaluation: its condition does not leave groups out.
    const field = content.indexOf('fieldName={"model"}');
    expect(field).toBeGreaterThan(-1);
    const shown = content.lastIndexOf(
      "(isFutureagiBuilt || alwaysShowModel)",
      field,
    );
    expect(shown).toBeGreaterThan(-1);
    expect(content.slice(shown, field)).not.toContain("isGroupEvals");
    expect(content.slice(field, field + 200)).toContain(
      'label={"Language Model"}',
    );
    // A group request sends that field's model.
    expect(form).toContain(
      "...(payload?.model ? { model: payload.model } : {}),",
    );
    expect(form).toMatch(/eval_group_id: selectedEval\?\.id,/);
  });

  it("the evaluation's model: its own page edits the model", () => {
    const page = source("evals", "components", "EvalDetailPage.jsx");
    const editor = source("evals", "components", "InstructionEditor.jsx");

    expect(page).toContain(
      'import InstructionEditor from "./InstructionEditor";',
    );
    expect(page).toContain("<InstructionEditor");
    expect(editor).toContain("<ModelSelector");
  });
});
