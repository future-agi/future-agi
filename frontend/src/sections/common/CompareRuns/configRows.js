// Config differences of any two runs (W5-6 B6, CP-07). Pure. The changes are
// exactly the chips of the one config diff (`configDiff(a, b, {all: true})`,
// import); this module only groups them and, for unchanged settings, prints
// the shared value from the run's config snapshot.
import { AXIS_NOUNS, configDiff } from "src/sections/common/Runs/configDiff";

/** Display groups, in order, and the configDiff axes each holds. */
export const CONFIG_GROUPS = Object.freeze([
  ["Model & parameters", ["kind", "framework", "model", "models", "params"]],
  ["Prompt", ["prompt"]],
  ["Tools & MCP", ["tools", "mcp", "permissions", "safety"]],
  ["Agent", ["agent", "code"]],
  ["Harness", ["harness"]],
  ["Limits", ["trials"]],
  ["Rows", ["dataset", "traces"]],
  ["Environment", ["execution", "endpoint", "voice", "capture"]],
  ["Evaluations", ["evals"]],
]);

const GROUP_OF = Object.fromEntries(
  CONFIG_GROUPS.flatMap(([title, axes]) => axes.map((a) => [a, title])),
);

const configOf = (run) => run?.config ?? null;
const listText = (xs) => (xs?.length ? xs.join(", ") : null);
const scalar = (v) =>
  v == null || v === ""
    ? null
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);

/** A setting's value on one config, as one line (null when it has none). */
export function settingValue(config, axis) {
  const c = config ?? {};
  switch (axis) {
    case "model":
      return (
        c.model?.id ?? c.harness?.model ?? c.agent?.nodes?.[0]?.model ?? null
      );
    case "params": {
      const p = c.model?.params ?? {};
      const keys = Object.keys(p);
      return keys.length ? keys.map((k) => `${k} ${p[k]}`).join(" · ") : null;
    }
    case "prompt":
      return c.prompt
        ? [c.prompt.name, c.prompt.version].filter(Boolean).join(" ")
        : null;
    case "tools":
      return listText((c.tools ?? []).map((t) => t.name ?? scalar(t)));
    case "mcp":
      return listText(
        (c.mcp?.servers ?? c.mcp ?? [])
          .map?.((s) => s?.name ?? scalar(s))
          ?.filter(Boolean),
      );
    case "agent":
      return c.agent
        ? [c.agent.name, c.agent.version].filter(Boolean).join(" ")
        : null;
    case "harness":
      return c.harness
        ? [c.harness.name, c.harness.version].filter(Boolean).join(" ")
        : null;
    case "trials": {
      const parts = [];
      if (c.trials != null)
        parts.push(`${c.trials} ${c.trials === 1 ? "try" : "tries"}`);
      Object.entries(c.limits ?? {}).forEach(([k, v]) => {
        if (v != null) parts.push(`${k.replace(/_/g, " ")} ${scalar(v)}`);
      });
      return parts.length ? parts.join(" · ") : null;
    }
    case "dataset":
      return c.dataset
        ? [
            c.dataset.version != null
              ? String(c.dataset.version).startsWith("v")
                ? c.dataset.version
                : `v${c.dataset.version}`
              : null,
            c.dataset.rows != null ? `${c.dataset.rows} rows` : null,
            listText(c.dataset.splits),
          ]
            .filter(Boolean)
            .join(" · ") || null
        : null;
    case "evals":
      return listText(
        (c.evals ?? []).map((e) =>
          [e.name ?? e.id, e.version].filter(Boolean).join(" "),
        ),
      );
    case "execution":
      return scalar(c.execution?.where ?? c.execution?.name ?? null);
    case "endpoint":
      return scalar(c.endpoint?.url ?? null);
    default:
      return null;
  }
}

const nameOf = (chip) =>
  chip.axis === "params" && chip.detail?.param
    ? chip.detail.param
    : AXIS_NOUNS[chip.axis] ?? chip.axis;

/**
 * L7c g6 (cfg:diff / cmp:task-setup-diff): a list (tools, MCP servers)
 * reads as names (`lookup_order, issue_refund`), an empty list as `none`.
 */
export const listSideText = (v) =>
  v.length
    ? v
        .map((x) =>
          x && typeof x === "object" ? x.name ?? JSON.stringify(x) : String(x),
        )
        .join(", ")
    : "none";

const sideText = (v) =>
  v == null || v === "none"
    ? "—"
    : Array.isArray(v)
      ? listSideText(v)
      : typeof v === "object"
        ? scalar(v)
        : String(v);

/**
 * One change = one setting: chips of the same setting with the same two
 * sides (configDiff emits one tools chip per tool) make one row.
 */
export const changeSignature = (chip) => {
  const d = chip?.detail ?? {};
  if (d.from === undefined && d.to === undefined) return null;
  return [
    chip.axis,
    d.param ?? d.field ?? d.eval ?? d.server ?? "",
    JSON.stringify(d.from ?? null),
    JSON.stringify(d.to ?? null),
  ].join("|");
};

/**
 * Grouped config rows of two runs.
 * @param {object} a  run A (R1 run with `config`)
 * @param {object} b  run B
 * @param {{differencesOnly?: boolean}} [opts]
 * @returns {{count: number, chips: object[], groups: Array<{title: string,
 *   rows: Array<{key: string, axis: string, name: string, a: string,
 *   b: string, changed: boolean, text: string, chip?: object}>}>}}
 *   `count` = the number of configDiff chips (the differences)
 */
export function configRows(a, b, { differencesOnly = true } = {}) {
  const all = configDiff(a, b, { all: true });
  const chips = all.filter((c) => !c.unchanged);
  const rows = [];
  const seen = new Set();
  chips.forEach((chip, i) => {
    const d = chip.detail ?? {};
    const hasSides = d.from !== undefined || d.to !== undefined;
    const sig = changeSignature(chip);
    if (sig && seen.has(sig)) return;
    if (sig) seen.add(sig);
    rows.push({
      key: `${chip.axis}:${i}`,
      axis: chip.axis,
      group: GROUP_OF[chip.axis] ?? "Model & parameters",
      name: nameOf(chip),
      a: hasSides ? sideText(d.from) : null,
      b: hasSides ? sideText(d.to) : null,
      changed: true,
      text: chip.text ?? chip.label,
      chip,
    });
  });
  if (!differencesOnly) {
    all
      .filter((c) => c.unchanged)
      .forEach((c) => {
        const v = settingValue(configOf(a), c.axis);
        if (v == null) return;
        rows.push({
          key: `${c.axis}:same`,
          axis: c.axis,
          group: GROUP_OF[c.axis] ?? "Model & parameters",
          name: AXIS_NOUNS[c.axis] ?? c.axis,
          a: v,
          b: v,
          changed: false,
          text: c.text,
        });
      });
  }
  const groups = CONFIG_GROUPS.map(([title]) => ({
    title,
    rows: rows.filter((r) => r.group === title),
  })).filter((g) => g.rows.length);
  // L7c g6: the count is the settings that differ (one row each).
  return { count: rows.filter((r) => r.changed).length, chips, groups };
}

/** `7 differences` / `1 difference` / `No differences`. */
export const differencesText = (n) =>
  n ? `${n} ${n === 1 ? "difference" : "differences"}` : "No differences";

/** The prompt's messages as text lines (`system: …`), for the line diff. */
export const promptLines = (messages) =>
  (messages ?? [])
    .map((m) =>
      typeof m === "string" ? m : `${m.role ?? "message"}: ${m.content ?? ""}`,
    )
    .join("\n");

/**
 * Task compare Setup extras (B6): one run's Temperature, Prompt version,
 * Dataset version and Evaluations, from its config snapshot.
 */
export function setupExtras(run) {
  const c = configOf(run);
  if (!c) return {};
  const temp = c.model?.params?.temperature;
  return {
    Temperature: temp == null ? null : String(temp),
    "Prompt version": settingValue(c, "prompt"),
    "Dataset version":
      c.dataset?.version == null
        ? null
        : String(c.dataset.version).startsWith("v")
          ? c.dataset.version
          : `v${c.dataset.version}`,
    Evaluations: settingValue(c, "evals"),
  };
}
