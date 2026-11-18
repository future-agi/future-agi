import { describe, it, expect } from "vitest";
import { getConditionalNodeDefaultValues } from "./common";

const allColumns = [{ headerName: "input", field: "col_input" }];

// Saved metadata is snake_case (the shape transformFormToApi writes): branch_type,
// branch_node_config. The transform must read those keys and rebuild camelCase form values.
describe("getConditionalNodeDefaultValues", () => {
  it("round-trips a saved run_prompt config into edit-form defaults", () => {
    const saved = {
      new_column_name: "verdict",
      config: [
        {
          branch_type: "if",
          condition: "{{col_input}} == 'yes'",
          branch_node_config: { type: "run_prompt", config: { prompt: "hi" } },
        },
      ],
    };

    const result = getConditionalNodeDefaultValues(saved, allColumns);

    expect(result.newColumnName).toBe("verdict");
    expect(result.config[0].branchType).toBe("if");
    expect(result.config[0].condition).toBe("{{input}} == 'yes'");
    expect(result.config[0].branchNodeConfig).toEqual({
      type: "run_prompt",
      config: { prompt: "hi" },
    });
  });

  it("loads a partial post-failure config without throwing", () => {
    const partial = {
      config: [
        { branch_type: "if", condition: null, branch_node_config: undefined },
        {
          branch_type: "else",
          condition: null,
          branch_node_config: { type: "run_prompt" },
        },
      ],
    };

    const result = getConditionalNodeDefaultValues(partial, allColumns);

    expect(result.config[0].condition).toBe("");
    expect(result.config[0].branchNodeConfig).toEqual({
      type: "",
      config: null,
    });
    expect(result.config[1].branchType).toBe("else");
    expect(result.config[1].branchNodeConfig).toEqual({
      type: "run_prompt",
      config: null,
    });
  });
});
