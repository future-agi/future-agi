// Run on these rows, from any door (R4-SPEC D24; lane F; frozen API). The
// only builder of the drawer's row / split / prompt deep-link keys; Ra's
// drawer reads them with parseRunOnRows. Pure.
//
//   /dashboard/develop/<dataset>?tab=experiments&new_experiment=1
//     &rows=<ids>|selection &split=<name> &source=<kind> &entry=<entryId>
//     &prompt_id=<id> &prompt_version=<n> &version_id=<dataset version id>
import { lastCustomerTurn } from "src/sections/common/Media/mediaRef";

/** More ids than this go through `rows=selection` + the store prefill. */
export const RUN_ON_ROWS_MAX_IDS = 50;

/**
 * Input names that mean "the user's message", in preference order (D24).
 * The session import and the drawer's mapping both use this list.
 */
export const INPUT_SYNONYMS = Object.freeze([
  "customer_message",
  "user_message",
  "message",
  "input",
  "question",
  "instruction",
  "query",
  "prompt",
]);

/**
 * Where a door sends the user: `{href, prefill}`. With more than 50 rows
 * the href says `rows=selection` and `prefill: {row_ids}` must go through
 * R3's `initiateCreateMode({prefill})` (develop-detail/states.jsx).
 * @param {{datasetId: string, rowIds?: string[], split?: string,
 *   source?: string, entry?: string, promptId?: string,
 *   promptVersionId?: string|number, datasetVersionId?: string}} args
 */
export function runOnRowsTarget({
  datasetId,
  rowIds,
  split,
  source,
  entry,
  promptId,
  promptVersionId,
  datasetVersionId,
} = {}) {
  const q = new URLSearchParams();
  q.set("tab", "experiments");
  q.set("new_experiment", "1");
  let prefill = null;
  const ids = (rowIds ?? []).filter(Boolean);
  if (ids.length > RUN_ON_ROWS_MAX_IDS) {
    q.set("rows", "selection");
    prefill = { row_ids: ids };
  } else if (ids.length) q.set("rows", ids.join(","));
  else if (split) q.set("split", split);
  if (source) q.set("source", source);
  if (entry) q.set("entry", entry);
  if (promptId) q.set("prompt_id", promptId);
  if (promptVersionId != null && promptVersionId !== "")
    q.set("prompt_version", String(promptVersionId));
  // v2.1 g8 (entry:run-on-rows:training-split): a door scoped to a saved
  // version (Training set) keeps it across a reload.
  if (datasetVersionId) q.set("version_id", String(datasetVersionId));
  const base = datasetId
    ? `/dashboard/develop/${encodeURIComponent(datasetId)}`
    : "/dashboard/develop";
  return { href: `${base}?${q.toString()}`, prefill };
}

/** The href of runOnRowsTarget (doors that never pass more than 50 ids). */
export const runOnRowsHref = (args) => runOnRowsTarget(args).href;

/**
 * Read the drawer deep-link keys from a search string or URLSearchParams.
 * @returns {{open: boolean, rows: string[]|"selection"|null,
 *   split: string|null, source: string|null, entry: string|null,
 *   agent: string|null, mode: string|null, form: "upload"|"connect"|null,
 *   promptId: string|null, promptVersion: string|null}}
 */
export function parseRunOnRows(search) {
  const q =
    search instanceof URLSearchParams
      ? search
      : new URLSearchParams(String(search ?? "").replace(/^\?/, ""));
  const rowsRaw = q.get("rows");
  let rows = null;
  if (rowsRaw === "selection") rows = "selection";
  else if (rowsRaw)
    rows = rowsRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  const form = q.get("form");
  return {
    open: q.get("new_experiment") === "1",
    rows,
    split: q.get("split"),
    source: q.get("source"),
    entry: q.get("entry"),
    agent: q.get("agent"),
    mode: q.get("mode"),
    form: form === "upload" || form === "connect" ? form : null,
    promptId: q.get("prompt_id"),
    promptVersion: q.get("prompt_version"),
    datasetVersionId: q.get("version_id"),
  };
}

const nameOf = (c) => (typeof c === "string" ? c : c?.name ?? "");
const norm = (s) =>
  String(s ?? "")
    .trim()
    .toLowerCase();
const CONVERSATION_NAMES = [
  "conversation",
  "messages",
  "turns",
  "transcript",
  "chat",
];
const isConversationColumn = (c) => {
  const type = typeof c === "string" ? null : c?.data_type ?? c?.type;
  return (
    CONVERSATION_NAMES.includes(norm(nameOf(c))) &&
    (!type || type === "json" || type === "conversation")
  );
};

/**
 * Which dataset column fills a prompt / agent variable (D24): the column
 * with the same name, else the first input-synonym column, else the last
 * customer turn of a conversation column.
 * @param {string} variable  `user_message`
 * @param {Array<string|{name: string, data_type?: string}>} columns
 * @param {{conversationColumn?: string, sample?: *}} [opts]  `sample` = a
 *   conversation value to check that it has a customer turn
 * @returns {{column: string, how: "same name"|"synonym"|"last customer turn",
 *   path?: string}|null}
 */
export function inputColumnFor(
  variable,
  columns,
  { conversationColumn, sample } = {},
) {
  const list = (columns ?? []).filter((c) => nameOf(c));
  const v = norm(variable);
  if (!v) return null;
  const same = list.find((c) => norm(nameOf(c)) === v);
  if (same) return { column: nameOf(same), how: "same name" };
  if (INPUT_SYNONYMS.includes(v)) {
    for (const syn of INPUT_SYNONYMS) {
      const hit = list.find((c) => norm(nameOf(c)) === syn);
      if (hit) return { column: nameOf(hit), how: "synonym" };
    }
    const conv =
      (conversationColumn &&
        list.find((c) => nameOf(c) === conversationColumn)) ??
      list.find(isConversationColumn);
    if (conv && (sample === undefined || lastCustomerTurn(sample) != null))
      return {
        column: nameOf(conv),
        how: "last customer turn",
        path: "last customer turn",
      };
  }
  return null;
}
