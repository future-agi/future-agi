const GROUPS = [
  {
    id: "rules",
    label: "Rules",
    tab: "contract",
    kinds: ["rule", "contract_field", "sub_goal"],
  },
  {
    id: "state",
    label: "Starting state and records",
    tab: "contract",
    kinds: ["world_state"],
  },
  { id: "tools", label: "Tools", tab: "contract", kinds: ["tool"] },
  { id: "actors", label: "Actors", tab: "contract", kinds: ["actor"] },
  {
    id: "scenarios",
    label: "Scenarios",
    tab: "scenarios",
    kinds: ["scenario"],
  },
  { id: "other", label: "Other", tab: "contract", kinds: ["source", "file"] },
];

const FIELD_LABEL = {
  rule: "Rules",
  contract_field: "Contract",
  sub_goal: "Sub-goals",
  world_state: "Starting state",
  tool: "Tools",
  actor: "Actors",
  scenario: "Scenarios",
  source: "Sources",
  file: "Files",
};

/** Undo rejects a change: it leaves the list. Keeping one accepts it at Save. */
export const keptChanges = (changes = []) =>
  changes.filter((one) => one.decision !== "rejected");

/** The kept changes in the design's order, under what they change. */
export const groupChanges = (changes = []) =>
  GROUPS.map(({ kinds, ...group }) => ({
    ...group,
    changes: changes.filter((one) => kinds.includes(one.kind)),
  })).filter((group) => group.changes.length);

const nameOf = (value) => {
  if (value === null || value === undefined || value === "") return "none";
  if (typeof value !== "object") return String(value);
  return value.name || value.title || value.label || value.id || "an item";
};

const fieldWords = (path) =>
  path.replace(/^\//, "").replaceAll("/", " · ").replaceAll("_", " ");

const rowCounts = ({ added, removed, changed }) =>
  [
    added && `${added} added`,
    removed && `${removed} removed`,
    changed && `${changed} changed`,
  ]
    .filter(Boolean)
    .join(", ");

/**
 * A change as before → after lines in the screens' words:
 * [{ sign: "+" | "−" | "~", field, text, before? }]. `before` is a reworded
 * whole item's old text, shown struck through above the new.
 */
export const diffLines = (change) => {
  const field = FIELD_LABEL[change.kind];
  const patch = change.patch || [];
  const read = Object.fromEntries(
    patch.filter((op) => op.op === "test").map((op) => [op.path, op.value]),
  );
  const lines = patch
    .filter((op) => op.op !== "test")
    .map((op) => {
      const old = read[op.path];
      if (op.path === "") {
        if (op.op === "add")
          return { sign: "+", field, text: nameOf(op.value) };
        return {
          sign: "~",
          field,
          before: nameOf(old),
          text: nameOf(op.value),
        };
      }
      const name = `${change.key} · ${fieldWords(op.path)}`;
      if (op.op === "remove") {
        return { sign: "−", field, text: `${name}: ${nameOf(old)}` };
      }
      if (op.op === "add") {
        return { sign: "+", field, text: `${name}: ${nameOf(op.value)}` };
      }
      return {
        sign: "~",
        field,
        text: `${name}: ${nameOf(old)} → ${nameOf(op.value)}`,
      };
    });
  if (change.action === "removed" && "" in read) {
    lines.push({ sign: "−", field, text: nameOf(read[""]) });
  }
  (change.rows || []).forEach((row) =>
    lines.push({
      sign: "~",
      field: "Tables",
      text: `${row.table} · ${rowCounts(row)}`,
    }),
  );
  return lines;
};

/** Every scenario a kept change needs proved again. */
export const recheckedKeys = (changes = []) => [
  ...new Set(keptChanges(changes).flatMap((one) => one.revalidate || [])),
];

/** The kept change that needs this one, so undoing it would break the save. */
export const undoBlock = (change, changes = []) => {
  const needs = keptChanges(changes).find((one) =>
    (one.depends_on || []).includes(change.id),
  );
  return needs ? { by: needs.summary } : null;
};

const fileOf = (source) =>
  (source?.used || []).find((one) => one.kind === "source");

/** Where a change came from, to read. */
export const sourceLine = (source = {}) => {
  const used = fileOf(source);
  const at = source.at || null;
  if (used) {
    return {
      kind: "file",
      file: used.source_id,
      where: used.locator || null,
      at,
    };
  }
  const kinds = { chat: "chat", nudge: "update" };
  return { kind: kinds[source.origin] || "hand", at };
};

/** Where a change came from, to go to. */
export const sourceLink = (source = {}) => {
  const used = fileOf(source);
  if (used) return { kind: "file", file: used.source_id };
  return { kind: source.origin === "chat" ? "chat" : "tab" };
};

const nextVersion = (version) => {
  const match = /^v(\d+)$/.exec(version?.label || "");
  return match ? `v${Number(match[1]) + 1}` : null;
};

/** What Save makes: the footer's and the bar's one line and its button. */
export const saveLine = (changes = [], versions) => {
  const kept = keptChanges(changes);
  const piece = kept.some((one) => one.piece === "environment")
    ? "environment"
    : "suite";
  const spec = kept.some((one) =>
    ["rule", "contract_field", "sub_goal"].includes(one.kind),
  );
  let kind = "scenarios";
  if (piece === "environment") kind = spec ? "spec" : "environment";
  return {
    kind,
    piece,
    next: nextVersion(versions?.defaults?.[piece]),
    rechecked: recheckedKeys(kept).length,
  };
};

/** How the saved version compares with past runs. */
export const comparabilityLine = (rechecked, total) => ({
  rechecked,
  exact: Math.max(0, total - rechecked),
});
