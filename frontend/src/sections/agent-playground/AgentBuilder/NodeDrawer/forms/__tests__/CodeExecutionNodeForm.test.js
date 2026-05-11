import { describe, expect, it } from "vitest";
import { getCodeExecutionEditorLanguage } from "../codeExecutionNodeFormUtils";

describe("CodeExecutionNodeForm", () => {
  it("maps TypeScript to Monaco's TypeScript grammar", () => {
    expect(getCodeExecutionEditorLanguage("typescript")).toBe("typescript");
  });

  it("keeps Python and JavaScript editor grammars exact", () => {
    expect(getCodeExecutionEditorLanguage("python")).toBe("python");
    expect(getCodeExecutionEditorLanguage("javascript")).toBe("javascript");
  });
});
