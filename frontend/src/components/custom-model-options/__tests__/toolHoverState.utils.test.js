import { describe, it, expect } from "vitest";
import { buildParameterRows } from "../toolHoverState.utils";

// Row lookup helper: find a row's value by its display heading.
const valueOf = (rows, heading) =>
  rows.find((r) => r.heading === heading)?.value;
const headings = (rows) => rows.map((r) => r.heading);

describe("buildParameterRows", () => {
  it("returns [] for null/undefined/empty config", () => {
    expect(buildParameterRows(null)).toEqual([]);
    expect(buildParameterRows(undefined)).toEqual([]);
    expect(buildParameterRows({})).toEqual([]);
  });

  it("renders null params as '-' and preserves 0", () => {
    const rows = buildParameterRows({ temperature: null, top_p: 0 });
    expect(valueOf(rows, "Temperature")).toBe("-");
    expect(valueOf(rows, "Top P")).toBe(0);
  });

  it("drops empty-string values (e.g. unset tool_choice)", () => {
    const rows = buildParameterRows({ tool_choice: "", temperature: 1 });
    expect(headings(rows)).not.toContain("Tool Choice");
    expect(valueOf(rows, "Temperature")).toBe(1);
  });

  it("shows tool_choice raw when set (scalars are not startCased)", () => {
    const rows = buildParameterRows({ tool_choice: "auto" });
    expect(valueOf(rows, "Tool Choice")).toBe("auto");
  });

  it("hides output_format for string output, shows response_format", () => {
    const rows = buildParameterRows({
      output_format: "string",
      response_format: "text",
    });
    expect(headings(rows)).not.toContain("Output Format");
    expect(valueOf(rows, "Response Format")).toBe("text");
  });

  it("shows output_format for non-string output, hides response_format", () => {
    const rows = buildParameterRows({
      output_format: "audio",
      response_format: "text",
    });
    expect(valueOf(rows, "Output Format")).toBe("audio");
    expect(headings(rows)).not.toContain("Response Format");
  });

  it("shows both formats when output_format is absent", () => {
    const rows = buildParameterRows({ response_format: "text" });
    expect(valueOf(rows, "Response Format")).toBe("text");
  });

  it("coerces an object response_format to a primitive (no React-child crash)", () => {
    const rows = buildParameterRows({
      output_format: "string",
      response_format: { id: "abc", name: "MySchema" },
    });
    expect(valueOf(rows, "Response Format")).toBe("MySchema");
  });

  it("labels a custom-schema response_format (uuid) as 'Custom'", () => {
    const rows = buildParameterRows({
      output_format: "string",
      response_format: "29edda1d-2e25-46af-870f-6b95a1381da8",
    });
    expect(valueOf(rows, "Response Format")).toBe("Custom");
  });

  it("excludes all structural keys", () => {
    const rows = buildParameterRows({
      model: "gpt-4o-mini",
      model_detail: { type: "chat" },
      tools: [{ id: "1" }],
      providers: "openai",
      id: "x",
      temperature: 1,
    });
    expect(headings(rows)).toEqual(["Temperature"]);
  });

  it("flattens top-level booleans and dropdowns", () => {
    const rows = buildParameterRows({
      booleans: { stream: false },
      dropdowns: { size: "auto" },
    });
    expect(valueOf(rows, "Stream")).toBe("false");
    expect(valueOf(rows, "Size")).toBe("Auto");
  });

  it("renders null booleans/dropdowns/sliders as '-' (consistent with scalars)", () => {
    const rows = buildParameterRows({
      booleans: { stream: null },
      dropdowns: { size: null },
      reasoning: { sliders: { budget: null } },
    });
    expect(valueOf(rows, "Stream")).toBe("-");
    expect(valueOf(rows, "Size")).toBe("-");
    expect(valueOf(rows, "Budget")).toBe("-");
  });

  it("flattens the nested reasoning object", () => {
    const rows = buildParameterRows({
      reasoning: {
        sliders: {},
        dropdowns: { reasoningEffort: "xhigh" },
        showReasoningProcess: true,
      },
    });
    expect(valueOf(rows, "Reasoning Effort")).toBe("Xhigh");
    expect(valueOf(rows, "Show Reasoning Process")).toBe("true");
  });

  it("does not throw on malformed reasoning", () => {
    expect(() => buildParameterRows({ reasoning: "oops" })).not.toThrow();
    expect(() => buildParameterRows({ reasoning: [] })).not.toThrow();
  });
});
