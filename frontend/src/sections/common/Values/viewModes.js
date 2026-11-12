// View modes of ValueViewer (R2-SPEC P6 §3.2): which modes apply to a
// sniffed value, why the others do not, and the per-field memory
// (`fa.valueView.<fieldKey>` in localStorage). Pure except the storage
// helpers, which never throw.

/**
 * integration review 2: a list longer than this is cut in Pretty (`first 200 of
 * N items`) and opens as a tree in ValueViewer's Auto (its `BIG_ARRAY`).
 */
export const BIG_LIST = 200;

/** `first 200 of 350 items` under a list Pretty cuts. */
export const cutListText = (n) =>
  `first ${BIG_LIST.toLocaleString("en-US")} of ${Number(n).toLocaleString("en-US")} items`;

export const VIEW_MODES = [
  "auto",
  "pretty",
  "chat",
  "markdown",
  // W3-2 B4 (D22): rendered HTML in a sandboxed frame; a form typed by the
  // column's declared schema (listed only when the column has one).
  "html",
  "form",
  "tree",
  "json",
  "yaml",
  "text",
  "raw",
];

export const MODE_LABEL = {
  auto: "Auto",
  pretty: "Pretty",
  chat: "Chat",
  markdown: "Markdown",
  html: "HTML",
  form: "Form",
  tree: "Tree",
  json: "JSON",
  yaml: "YAML",
  text: "Text",
  raw: "Raw",
};

/** IT-R2 G5 P6: one-line hints in the View as menu. */
export const MODE_HINT = {
  auto: "best view for this value",
  pretty: "formatted keys and values",
  chat: "messages as a conversation",
  markdown: "rendered text",
  html: "rendered page; scripts never run",
  form: "fields from the declared schema",
  tree: "expandable keys",
  json: "exact JSON text",
  yaml: "the same data as YAML",
  text: "plain text",
  raw: "the stored value, unchanged",
};

const STRUCTURED = new Set([
  "object",
  "array",
  "chat",
  "images",
  "embedding",
  "ref",
]);
const STRINGY = new Set(["text", "markdown", "yaml_text"]);

// Tags that make text HTML (a `<b>`, a `<table>`, a `<script>`), not a
// stray `<` in prose.
const HTML_TAG =
  /<\/?(html|head|body|div|span|p|br|hr|b|i|u|s|em|strong|small|sub|sup|mark|a|img|ul|ol|li|dl|dt|dd|table|thead|tbody|tfoot|tr|td|th|caption|h[1-6]|pre|code|blockquote|section|article|header|footer|nav|main|aside|figure|figcaption|details|summary|script|style|iframe|form|input|button|label|select|option|textarea|font|center|svg)(\s[^<>]*)?\/?>/i;

/** True for text that holds at least one HTML tag. */
export function isHtmlLike(value) {
  return typeof value === "string" && HTML_TAG.test(value);
}

/**
 * `{mode: null | reason}` — null when the mode applies to the value.
 * `form` is a key only when the column has a declared schema
 * (`{form: true}`); without one the mode is not listed at all.
 * @param {{kind: string}} sniffed  sniffValue result
 * @param {{form?: boolean}} [opts]
 */
export function modeAvailability(sniffed, { form = false } = {}) {
  const kind = sniffed?.kind ?? "empty";
  const structured = STRUCTURED.has(kind);
  const yamlText = kind === "yaml_text";
  return {
    auto: null,
    // integration review (RV-02): a session object read as chat (`{turns|messages,
    // …}`, `meta.wrapped`) is still an object, so Pretty applies.
    pretty:
      kind === "object" || kind === "ref" || sniffed?.meta?.wrapped
        ? null
        : "Pretty needs an object",
    chat:
      kind === "chat"
        ? null
        : "Chat needs a list of messages, or an object with turns or messages",
    markdown: STRINGY.has(kind) ? null : "Markdown needs text",
    html:
      STRINGY.has(kind) && isHtmlLike(sniffed?.normalized)
        ? null
        : "HTML needs text with HTML tags",
    ...(form ? { form: null } : {}),
    tree: structured || yamlText ? null : "Tree needs an object or a list",
    json:
      structured || yamlText || ["number", "boolean"].includes(kind)
        ? null
        : "JSON needs structured data",
    yaml: structured || yamlText ? null : "YAML needs structured data",
    text: kind === "empty" ? "Nothing to show" : null,
    raw: null,
  };
}

/** The mode to render: `wanted` when it applies, else `auto`. */
export function effectiveMode(wanted, sniffed, opts) {
  if (!wanted || !VIEW_MODES.includes(wanted)) return "auto";
  const availability = modeAvailability(sniffed, opts);
  if (!(wanted in availability)) return "auto";
  return availability[wanted] ? "auto" : wanted;
}

const KEY = (fieldKey) => `fa.valueView.${fieldKey}`;
export const FIELD_VIEW_EVENT = "fa:field-view";

/** Stored mode of a field, or null. */
export function readFieldView(fieldKey) {
  if (!fieldKey) return null;
  try {
    const v = window.localStorage.getItem(KEY(fieldKey));
    return VIEW_MODES.includes(v) ? v : null;
  } catch {
    return null;
  }
}

/** Remember a field's mode (`auto` clears it). */
export function writeFieldView(fieldKey, mode) {
  if (!fieldKey) return;
  try {
    if (!mode || mode === "auto") window.localStorage.removeItem(KEY(fieldKey));
    else window.localStorage.setItem(KEY(fieldKey), mode);
  } catch {
    // storage unavailable: the choice lasts for this page only
  }
  try {
    window.dispatchEvent(
      new CustomEvent(FIELD_VIEW_EVENT, { detail: { fieldKey, mode } }),
    );
  } catch {
    // no window (tests)
  }
}

/**
 * dt-10: the copy format of what a view shows — a field's copy
 * icon copies that; the `Copy as` menu keeps the other formats.
 */
export function copyFormatOfView(view) {
  if (view === "yaml") return "yaml";
  if (view === "json" || view === "tree") return "json";
  return "value";
}
