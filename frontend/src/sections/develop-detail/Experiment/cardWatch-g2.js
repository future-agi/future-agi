// L7c g2 (legacy:r4-run-card-kept): a run card reads its own values from
// the form's card list by its current index. A `useWatch` on
// `promptConfig.<i>.x` keeps the value it read under the old index when a
// card above it is removed (the change event reaches it before it re-renders
// with its new prefix), so after removing a middle card the cards below
// showed their neighbour's run and the last one a blank `Run`.
import { useWatch } from "react-hook-form";

const CARD_PREFIX = /^([A-Za-z_][\w]*)\.(\d+)$/;

/** The value at `path` (`a.b`) of an object, or undefined (pure). */
export const valueAt = (obj, path) =>
  String(path ?? "")
    .split(".")
    .filter(Boolean)
    .reduce((v, k) => (v == null ? undefined : v[k]), obj);

/**
 * The card's value at `path` (`r4`, `http.url`, `limits.tries`), read from
 * the whole card list by the card's current index.
 * @param {object} control  react-hook-form control
 * @param {string} fieldPrefix  `promptConfig.<index>`
 * @param {string} path
 */
export function useCardValue(control, fieldPrefix, path) {
  const m = CARD_PREFIX.exec(String(fieldPrefix ?? ""));
  const list = useWatch({ control, name: m ? m[1] : String(fieldPrefix) });
  const own = useWatch({ control, name: `${fieldPrefix}.${path}` });
  if (!m || !Array.isArray(list)) return own;
  return valueAt(list[Number(m[2])], path);
}
