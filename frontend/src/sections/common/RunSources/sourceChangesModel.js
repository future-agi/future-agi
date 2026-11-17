// options-truth L7b G3 (info:rerun-dialog:no-params): Rerun with changes of
// an Already running run shows the run's own fields (HTTP endpoint: URL,
// method, body, output path; A2A agent: skill; MCP tool: side effects) and
// posts the edited ones as changes `{axis, field, value}`, which the rerun
// applies to the new run's config. Pure.

export const SOURCE_KINDS = ["http", "a2a", "mcp_tool"];

export const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH"];

export const SIDE_EFFECTS = [
  { value: "real", label: "Run for real" },
  { value: "stubs", label: "Use tool stubs" },
  { value: "dry_run", label: "Dry run, no tool calls" },
];

/** The source kind of a run config, or null (code at a remote URL = http). */
export function sourceKindOfConfig(config = {}) {
  const kind = config?.kind ?? null;
  if (kind === "http" && config.endpoint) return "http";
  if (kind === "a2a" && config.a2a) return "a2a";
  if (kind === "mcp_tool" && config.mcp_tool) return "mcp_tool";
  if (kind === "code" && config.execution?.mode === "remote" && config.endpoint)
    return "http";
  return null;
}

/** The editable fields of a source config, as the dialog starts them. */
export function sourceFieldsOf(config = {}) {
  const kind = sourceKindOfConfig(config);
  if (kind === "http") {
    const e = config.endpoint ?? {};
    return {
      url: String(e.url ?? ""),
      method: String(e.method ?? "POST").toUpperCase(),
      body_template: String(e.body_template ?? ""),
      output_path: String(e.output_path ?? "$.reply"),
    };
  }
  if (kind === "a2a") return { skill_id: config.a2a?.skill_id ?? "" };
  if (kind === "mcp_tool")
    return {
      side_effects:
        config.mcp_tool?.side_effects ?? config.safety?.side_effects ?? "stubs",
    };
  return null;
}

/** The skills an A2A run's agent card lists, `{value, label}`. */
export function a2aSkillOptions(config = {}) {
  const skills = config?.a2a?.card?.skills ?? [];
  return skills.map((s) => ({ value: s.id, label: s.name ?? s.id }));
}

const AXIS = { http: "endpoint", a2a: "a2a", mcp_tool: "mcp_tool" };

/**
 * The changes a draft makes against the run's fields: one `{axis, field,
 * value}` per edited field (an empty URL or skill is never sent).
 */
export function sourceChangesOf(config = {}, draft = {}) {
  const kind = sourceKindOfConfig(config);
  const before = sourceFieldsOf(config);
  if (!kind || !before) return [];
  return Object.entries(draft ?? {})
    .filter(([field, value]) => field in before && value !== before[field])
    .filter(
      ([field, value]) =>
        !["url", "skill_id"].includes(field) || String(value ?? "").trim(),
    )
    .map(([field, value]) => ({
      axis: AXIS[kind],
      field,
      value: field === "url" ? String(value).trim() : value,
    }));
}

// The words each field reads as in the started toast.
const FIELD_WORDS = {
  url: "URL",
  method: "method",
  body_template: "body",
  output_path: "output path",
  skill_id: "skill",
  side_effects: "side effects",
};

/** `URL → https://… · method → PUT` for the started toast (pure). */
export function sourceChangeLine(changes = []) {
  return changes
    .map(
      (c) =>
        `${FIELD_WORDS[c.field] ?? String(c.field).replace(/_/g, " ")} → ${c.value}`,
    )
    .join(" · ");
}
