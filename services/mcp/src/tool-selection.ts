import type { GeneratedTool } from "./types.js";

export interface ToolSelection {
  features: ReadonlySet<string>;
  tools: ReadonlySet<string>;
  readonly: boolean;
}

export class ToolSelectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolSelectionError";
  }
}

function commaSeparated(values: string[]): Set<string> {
  return new Set(
    values
      .flatMap((value) => value.split(","))
      .map((value) => value.trim())
      .filter(Boolean),
  );
}

export function selectionFromUrl(url: URL): ToolSelection {
  const readonlyValue = url.searchParams.get("readonly");
  if (readonlyValue !== null && !["true", "false"].includes(readonlyValue)) {
    throw new ToolSelectionError("readonly must be true or false");
  }
  return {
    features: commaSeparated(url.searchParams.getAll("features")),
    tools: commaSeparated(url.searchParams.getAll("tools")),
    readonly: readonlyValue === "true",
  };
}

export function selectTools(
  catalog: readonly GeneratedTool[],
  selection?: ToolSelection,
): GeneratedTool[] {
  const enabled = catalog.filter((tool) => tool.enabled);
  if (!selection) return enabled;

  const availableFeatures = new Set(enabled.map((tool) => tool.group));
  const availableTools = new Set(enabled.map((tool) => tool.name));
  const unknownFeatures = [...selection.features].filter(
    (feature) => !availableFeatures.has(feature),
  );
  const unknownTools = [...selection.tools].filter((tool) => !availableTools.has(tool));
  if (unknownFeatures.length > 0 || unknownTools.length > 0) {
    throw new ToolSelectionError(
      [
        unknownFeatures.length > 0
          ? `Unknown features: ${unknownFeatures.sort().join(", ")}`
          : "",
        unknownTools.length > 0 ? `Unknown tools: ${unknownTools.sort().join(", ")}` : "",
      ]
        .filter(Boolean)
        .join("; "),
    );
  }

  const hasAllowlist = selection.features.size > 0 || selection.tools.size > 0;
  return enabled.filter((tool) => {
    if (selection.readonly && tool.risk !== "read") return false;
    if (!hasAllowlist) return true;
    return selection.features.has(tool.group) || selection.tools.has(tool.name);
  });
}
