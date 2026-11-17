import { describe, expect, it } from "vitest";
import {
  compactDefinition,
  unsavedDefinitionConfig,
} from "../unsavedDefinition";

describe("unsaved definition for the playground request", () => {
  it("keeps set fields, cleared values and false, and drops unset ones", () => {
    expect(
      compactDefinition({
        model: "gpt-4o",
        instructions: undefined,
        choice_scores: null,
        multi_choice: false,
        few_shot_examples: [],
      }),
    ).toEqual({
      model: "gpt-4o",
      choice_scores: null,
      multi_choice: false,
      few_shot_examples: [],
    });
  });

  it("leaves out a blank model, which the template update refuses", () => {
    expect(compactDefinition({ model: "", code: "" })).toEqual({ code: "" });
  });

  it("puts the definition under unsaved_definition, and nothing without one", () => {
    expect(unsavedDefinitionConfig({ model: "gpt-4o" })).toEqual({
      unsaved_definition: { model: "gpt-4o" },
    });
    expect(unsavedDefinitionConfig(null)).toEqual({});
  });
});
