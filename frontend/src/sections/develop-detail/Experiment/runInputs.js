// v2.1 (data:nothing-to-map, review:rows-send): what each row sends to a
// run that has no {{variables}}: an agent graph takes the row's own message,
// an A2A agent the parts its card maps (text, data, context), an MCP tool the
// arguments its card maps. The same fields the create body carries and the
// run engine reads (r4-sources-runs a2aTry / mcpTry, runEngine ownRequestOf).
// Pure.
import { JSON_PATH_SEPARATOR } from "./variableMapping";

// The row columns an agent graph run reads as the request, in order.
const AGENT_REQUEST_COLUMNS = ["msg", "customer_message", "instruction"];

/** `conversation.$[-1].content` for `<colId>::$[-1].content`; null if unknown. */
const nameOf = (columns, ref) => {
  if (!ref) return null;
  const [id, path] = String(ref).split(JSON_PATH_SEPARATOR);
  const col = (columns ?? []).find((c) => c?.id === id || c?.name === id);
  return col ? `${col.name}${path ? `.${path}` : ""}` : null;
};

const argText = (k, m, columns) => {
  if (!m || m.kind === "empty") return `${k} (empty)`;
  if (m.kind === "constant") return `${k} = "${m.value ?? ""}"`;
  const col = nameOf(columns, m.column ?? m.column_id ?? m.value);
  return col ? `${k} ← ${col}` : `${k} (empty)`;
};

/**
 * The inputs one card's run sends per row, or null for cards mapped by
 * {{variables}} (prompt, HTTP) or not configured yet.
 * @param {object} card  promptConfig item
 * @param {{id: string, name: string}[]} columns  the dataset's columns
 * @returns {string[]|null} e.g. `["text ← customer_message", "data: order_id"]`
 */
export function runInputsOf(card, columns = []) {
  const type = card?.targetType;
  if (type === "agent") {
    const col = AGENT_REQUEST_COLUMNS.find((n) =>
      (columns ?? []).some((c) => c?.name === n),
    );
    return [
      col
        ? `${col} (the row's own message)`
        : "the row's own message or instruction",
    ];
  }
  if (type === "a2a") {
    const a = card.a2a ?? {};
    const text = nameOf(columns, a.text_column);
    if (!text && !(a.data_columns ?? []).length) return null;
    const out = [text ? `text ← ${text}` : "text (empty)"];
    const data = (a.data_columns ?? [])
      .map((id) => nameOf(columns, id))
      .filter(Boolean);
    if (data.length) out.push(`data: ${data.join(", ")}`);
    const ctx = nameOf(columns, a.context_column);
    if (ctx) out.push(`context id ← ${ctx}`);
    return out;
  }
  if (type === "mcp_tool") {
    const m = card.mcp_tool ?? {};
    if (!m.tool) return null;
    const args = Object.entries(m.arguments ?? {}).map(([k, v]) =>
      argText(k, v, columns),
    );
    return args.length ? args : ["no arguments"];
  }
  return null;
}

/** `#A Support Agent: customer_message (the row's own message)` lines. */
export function runInputLines(cards = [], columns = [], { labelOf } = {}) {
  return (cards ?? [])
    .map((card, i) => {
      const inputs = runInputsOf(card, columns);
      if (!inputs) return null;
      const label = labelOf ? labelOf(card, i) : card?.name ?? "";
      return {
        index: i,
        text: `${label ? `${label}: ` : ""}each row sends ${inputs.join(" · ")}`,
      };
    })
    .filter(Boolean);
}

/**
 * v2.1 (vars:coverage): a mapping's coverage (`{present, total, …}`, counted
 * over the whole latest version) when its total is the rows in scope, else
 * null (a split, a door's rows, a sample or an older version). Pure.
 */
export function coverageInScope(coverage, rowsInScope) {
  if (!coverage) return null;
  const n = Number(rowsInScope);
  if (!Number.isFinite(n) || n <= 0) return coverage;
  return Number(coverage.total) === n ? coverage : null;
}
