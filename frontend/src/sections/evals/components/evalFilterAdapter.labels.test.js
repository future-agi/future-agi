import { describe, expect, it } from "vitest";
import { EVAL_FILTER_FIELDS, rowsToFilters } from "./evalFilterAdapter";

const fieldOf = (id) => EVAL_FILTER_FIELDS.find((field) => field.id === id);

// The filter's options read like the picker table beside it: EvalTypeBadge
// (Eval Type), the Output column, TypeBadge (Type) and Created By.
describe("evalFilterAdapter option labels", () => {
  it("labels eval types the way the Eval Type column does", () => {
    expect(fieldOf("eval_type").options).toEqual([
      { value: "llm", label: "LLM" },
      { value: "code", label: "Code" },
      { value: "agent", label: "Agent" },
    ]);
  });

  it("labels output types the way the Output column does", () => {
    expect(fieldOf("output_type").options).toEqual([
      { value: "pass_fail", label: "Pass/fail" },
      { value: "percentage", label: "Percentage" },
      { value: "deterministic", label: "Choices" },
    ]);
  });

  it("labels template types the way the Type column does", () => {
    expect(fieldOf("template_type").options).toEqual([
      { value: "single", label: "Single" },
      { value: "composite", label: "Composite" },
    ]);
  });

  it("labels owners the way the Created By column does", () => {
    expect(fieldOf("owner").options).toEqual([
      { value: "user", label: "User" },
      { value: "system", label: "System" },
    ]);
  });

  it("sends the raw API values of the picked options", () => {
    const pick = (id, options) => ({
      field: id,
      operator: fieldOf(id).operators[0],
      value: options.map((option) => option.value),
    });
    expect(
      rowsToFilters([
        pick("eval_type", fieldOf("eval_type").options),
        pick("output_type", fieldOf("output_type").options),
        pick("template_type", fieldOf("template_type").options.slice(1)),
        pick("owner", fieldOf("owner").options.slice(1)),
      ]),
    ).toEqual({
      eval_type: ["llm", "code", "agent"],
      output_type: ["pass_fail", "percentage", "deterministic"],
      template_type: "composite",
      owner: "system",
    });
  });
});
