import { describe, it, expect } from "vitest";
import { getDefaultValues } from "../EditTool.utils";

describe("getDefaultValues (TH-5930 edit-mode input schema)", () => {
  it("prefills the JSON schema from a snake_case json tool", () => {
    const tool = {
      name: "one",
      description: "asndlkjaslkdasd",
      config_type: "json",
      config: { type: "function", function: { name: "one" } },
    };

    const values = getDefaultValues(tool);

    expect(values.config_type).toBe("json");
    expect(values.name).toBe("one");
    // the saved config is loaded into the editor, not the empty default template
    expect(values.inputSchema.json).toBe(JSON.stringify(tool.config, null, 2));
    expect(values.inputSchema.json).toContain('"name": "one"');
  });

  it("prefills the YAML schema from a snake_case yaml tool", () => {
    const tool = {
      name: "two",
      description: "desc",
      config_type: "yaml",
      yaml_config: "type: function\nfunction:\n  name: two\n",
    };

    const values = getDefaultValues(tool);

    expect(values.config_type).toBe("yaml");
    expect(values.inputSchema.yaml).toBe(tool.yaml_config);
    expect(values.inputSchema.yaml).toContain("name: two");
  });

  it("falls back to default templates for create mode (no tool)", () => {
    const values = getDefaultValues(null);

    expect(values.config_type).toBe("json");
    expect(values.name).toBe("");
    expect(values.inputSchema.json).toContain('"type": "function"');
    expect(values.inputSchema.yaml).toContain("type: function");
  });

  it("does not pick up legacy camelCase keys (regression guard)", () => {
    // A tool shaped the old camelCase way must NOT populate the editor —
    // it would leave the schema empty, which is the bug we are guarding.
    const camelTool = {
      name: "x",
      configType: "json",
      config: { a: 1 },
    };

    const values = getDefaultValues(camelTool);

    expect(values.config_type).toBeUndefined();
    expect(values.inputSchema.json).not.toContain('"a": 1');
  });
});
