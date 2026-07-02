import { describe, it, expect } from "vitest";
import { getConditionalNodeDefaultValues } from "./common";

const allColumns = [{ headerName: "input", field: "col_input" }];

describe("getConditionalNodeDefaultValues", () => {
  it("maps a well-formed saved config into edit-form defaults", () => {
    const saved = {
      newColumnName: "verdict",
      config: [
        {
          branchType: "if",
          condition: "{{col_input}} == 'yes'",
          branchNodeConfig: { type: "run_prompt", config: { prompt: "hi" } },
        },
      ],
    };

    const result = getConditionalNodeDefaultValues(saved, allColumns);

    expect(result.newColumnName).toBe("verdict");
    expect(result.config[0].condition).toBe("{{input}} == 'yes'");
    expect(result.config[0].branchNodeConfig).toEqual({
      type: "run_prompt",
      config: { prompt: "hi" },
    });
  });

  it("loads a partial post-failure config without throwing", () => {
    const partial = {
      newColumnName: "verdict",
      config: [
        { branchType: "if", condition: null, branchNodeConfig: undefined },
        {
          branchType: "else",
          condition: null,
          branchNodeConfig: { type: "run_prompt" },
        },
      ],
    };

    const result = getConditionalNodeDefaultValues(partial, allColumns);

    expect(result.config[0].condition).toBe("");
    expect(result.config[0].branchNodeConfig).toEqual({
      type: "",
      config: null,
    });
    expect(result.config[1].condition).toBe("");
    expect(result.config[1].branchNodeConfig).toEqual({
      type: "run_prompt",
      config: null,
    });
  });
});
