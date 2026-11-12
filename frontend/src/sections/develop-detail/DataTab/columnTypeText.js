// What fits each column type, in the words the type-change copy uses
// ("250 values that are not document links …"). Edit Column Type's confirm
// and the prototype's update_column_type answers read this one table.

/** The types Edit Column Type offers, with their labels. */
export const COLUMN_TYPES = Object.freeze([
  { label: "Text", value: "text" },
  { label: "Boolean", value: "boolean" },
  { label: "Integer", value: "integer" },
  { label: "Float", value: "float" },
  { label: "JSON", value: "json" },
  { label: "Array", value: "array" },
  { label: "Datetime", value: "datetime" },
  { label: "Image", value: "image" },
  { label: "Images", value: "images" },
  { label: "Audio", value: "audio" },
  { label: "Document", value: "document" },
]);

/** `Document` for `document`; the type itself when unknown. */
export const columnTypeLabel = (type) =>
  COLUMN_TYPES.find((t) => t.value === type)?.label ?? type;

// [one, many]
const FIT_WORDS = Object.freeze({
  text: ["text", "text"],
  boolean: ["true / false", "true / false"],
  integer: ["a whole number", "whole numbers"],
  float: ["a number", "numbers"],
  json: ["JSON", "JSON"],
  array: ["an array", "arrays"],
  datetime: ["a date", "dates"],
  image: ["an image link", "image links"],
  images: ["an image link", "image links"],
  audio: ["an audio link", "audio links"],
  document: ["a document link", "document links"],
});

/** `a document link` (n = 1) or `document links`; null for an unknown type. */
export const fitWords = (type, n = 2) => {
  const words = FIT_WORDS[String(type ?? "").toLowerCase()];
  if (!words) return null;
  return n === 1 ? words[0] : words[1];
};

const count = (n) => Number(n).toLocaleString("en-US");

/**
 * The confirm of a type change that empties what does not fit
 * (`force_update`). `invalidCount` is the preview's `invalid_count`:
 * undefined while it is read, null when the read failed.
 * Columns are not versioned, so the values are emptied in every version, and
 * restoring a version cannot bring them back: the copy says both.
 */
export function typeChangeConfirmText({ invalidCount, type, label }) {
  const name = label ?? type;
  if (invalidCount === undefined) return `Checking which values fit ${name}…`;
  const kind = String(type ?? "").toLowerCase();
  const kept = "Values that fail conversion are kept and marked with an error.";
  const media = " Media content or quality can be lost during conversion without an error.";
  const note =
    {
      integer: " Fractional parts are discarded, and large numbers can lose precision without an error.",
      float: " Numbers can be rounded or changed during conversion without an error.",
      json: " Values can be changed during conversion, including by automatic repair, without an error.",
      array: " Values inside the array can be changed during conversion without an error.",
      datetime: " Some timestamps can be changed during conversion, for example read as a different date, without an error.",
      image: media,
      audio: media,
    }[kind] ?? "";
  const outcome =
    kind === "boolean"
      ? "Values that aren't recognized as true or false become false."
      : kind === "images"
        ? `${kept} False, zero, null and empty entries can be dropped without an error.${media}`
        : `${kept}${note}`;
  const change = `Changing this column to ${name} updates the current dataset and converts its values. ${outcome} This type change has no undo.`;
  return invalidCount === null ? `Couldn't check the values. ${change}` : change;
}

/**
 * Restore version (K4F merge M12): the values of `vN` that a column type
 * change emptied (N4 `emptied_by_type_change`). They are empty in vN too,
 * so the restore cannot bring them back. Null when there are none.
 */
export function typeChangeEmptiedText(list, versionNumber) {
  const items = (list ?? []).filter((x) => Number(x?.values) > 0);
  if (!items.length) return null;
  const valuesOf = (n) => `${count(n)} ${n === 1 ? "value" : "values"}`;
  if (items.length === 1) {
    const [x] = items;
    const one = Number(x.values) === 1;
    return `${valuesOf(x.values)} of v${versionNumber} in “${x.column_name}” ${
      one ? "was" : "were"
    } emptied when its type changed to ${columnTypeLabel(
      x.data_type,
    )}, so ${one ? "it stays" : "they stay"} empty.`;
  }
  const parts = items.map(
    (x, i) =>
      `${i === 0 ? valuesOf(x.values) : count(x.values)} in “${x.column_name}”`,
  );
  const listText = `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
  return `Column type changes emptied ${listText} of v${versionNumber}, so they stay empty.`;
}
