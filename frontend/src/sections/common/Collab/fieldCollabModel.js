// HO D1 / D2: one field's collaboration doors in the Row view (the field
// card's ⋯ menu: Translate to… and Comment on this field) and the grid
// cell's `Translate cell…` deep link. Pure.
import { commentDeepLink } from "./commentModel";
import { sectionFields, sectionTargets } from "./translateModel";

// The Row view param that opens one field's Translate to… (D2).
export const TRANSLATE_FIELD_PARAM = "rtranslate";
// The Activity › Comments param that picks the composer's anchor (D1).
export const COMMENT_ANCHOR_PARAM = "ranchor";

/** The composer anchor key of a field (CommentComposer / fieldAnchorOptions). */
export const fieldAnchorKey = (columnId) => `field:${columnId}`;

/** Comment on this field: Activity › Comments, caret in the composer, on it. */
export function fieldCommentDeepLink(
  params,
  rowId,
  columnId,
  now = Date.now(),
) {
  const next = commentDeepLink(params, rowId, now);
  next.set(COMMENT_ANCHOR_PARAM, fieldAnchorKey(columnId));
  next.delete(TRANSLATE_FIELD_PARAM);
  return next;
}

/** Translate cell…: the row, its Row tab, that field's Translate to… open. */
export function translateCellDeepLink(params, rowId, columnId) {
  const next = new URLSearchParams(params);
  next.set("row", String(rowId));
  next.set("rowTab", "row");
  next.set(TRANSLATE_FIELD_PARAM, String(columnId));
  return next;
}

/** The deep link handled: drop the param so a reload does not reopen it. */
export function withoutTranslateParam(params) {
  const next = new URLSearchParams(params);
  next.delete(TRANSLATE_FIELD_PARAM);
  return next;
}

/**
 * The targets one field offers on this row (the translate read's own
 * `available`, by column name), `[{code, name, fields}]`.
 */
export const fieldTargets = (info, name) =>
  sectionTargets(sectionFields(info?.fields, [name]), info?.languages);

/** Why a field has nothing to translate (the deep link's honest answer). */
export function noTargetText(info, name, rowLabel) {
  const f = (info?.fields ?? []).find((x) => x.name === name);
  const where = rowLabel ? ` on ${rowLabel}` : "";
  if (!f) return `${name} is not a text field that can be translated.`;
  if (!f.language)
    return `${name} is empty${where}, so there is nothing to translate.`;
  return `No translation of ${name}${where} from ${f.language_name ?? f.language} is available.`;
}

/**
 * The grid offers `Translate cell…` on a text field the translate read
 * covers (a user column of type text with a value).
 */
export const canTranslateCell = (colDef, value) =>
  colDef?.dataType === "text" &&
  colDef?.col?.is_user_data !== false &&
  !["evaluation", "evaluation_reason", "run_prompt"].includes(
    colDef?.originType ?? colDef?.col?.origin_type,
  ) &&
  typeof value === "string" &&
  value.trim() !== "" &&
  !looksEnglish(value);

// v2.1 g8 (translate:field-or-cell): the grid offers `Translate cell…` only
// on text that is not English (a non-Latin script, accented letters, or
// more of another language's common words than English ones); English
// cells could only end in "No translation … is available".
const EN_WORDS = new Set(
  "the and is are was to of my i you your it this that for with on in have has not can please order what when where why how".split(
    " ",
  ),
);
const OTHER_WORDS = new Set(
  "el la los las de que y en un una mi por para con no es se lo pedido hola gracias der die das und ist nicht ich mein meine bitte bestellung le les des et est je pas mon ma une pour commande merci o os um uma meu minha obrigado ciao grazie il di che non sono het een niet ik mijn kya hai nahi mera meri aap kripya".split(
    " ",
  ),
);
export function looksEnglish(text) {
  const t = String(text ?? "");
  // A letter outside Latin (Devanagari, CJK, Arabic, Cyrillic, …).
  if (/[^\p{Script=Latin}\P{L}]/u.test(t)) return false;
  if (/[áéíóúñüöäßàèêçãõâôœ¿¡]/i.test(t)) return false;
  const words = t.toLowerCase().match(/[a-z']+/g) ?? [];
  let en = 0;
  let other = 0;
  words.forEach((w) => {
    if (EN_WORDS.has(w)) en += 1;
    if (OTHER_WORDS.has(w)) other += 1;
  });
  // Short values ("hola") count with one word.
  return !(other > en && (other >= 2 || words.length <= 2));
}
