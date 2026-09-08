import { describe, expect, it } from "vitest";

import { generatedTools } from "../src/generated/tools.generated.js";
import {
  selectTools,
  selectionFromUrl,
  ToolSelectionError,
} from "../src/tool-selection.js";

describe("per-connection tool selection", () => {
  it("filters by feature group", () => {
    const selection = selectionFromUrl(
      new URL("https://mcp.example.test/mcp?features=observability"),
    );
    expect(selectTools(generatedTools, selection).map((tool) => tool.name).sort()).toEqual([
      "list_traces",
      "list_tracing_projects",
    ]);
  });

  it("unions feature groups and exact tool names", () => {
    const selection = selectionFromUrl(
      new URL(
        "https://mcp.example.test/mcp?features=datasets&tools=list_evaluation_groups",
      ),
    );
    expect(selectTools(generatedTools, selection).map((tool) => tool.name).sort()).toEqual([
      "list_datasets",
      "list_evaluation_groups",
    ]);
  });

  it("fails closed for unknown filters", () => {
    const selection = selectionFromUrl(
      new URL("https://mcp.example.test/mcp?tools=does_not_exist"),
    );
    expect(() => selectTools(generatedTools, selection)).toThrow(ToolSelectionError);
  });

  it("validates readonly instead of silently accepting typos", () => {
    expect(() =>
      selectionFromUrl(new URL("https://mcp.example.test/mcp?readonly=yes")),
    ).toThrow(/readonly must be true or false/u);
  });
});
