import { isNil, isEmpty } from "lodash";

// Values are per-type composites ({ rating }, { text }, ...); a cleared field
// keeps the key with an empty inner value, so check inner emptiness per type.
export const isLabelValueEmpty = (type, v) => {
  if (isNil(v)) return true;
  switch (type) {
    case "star":
      return !v.rating;
    case "categorical":
      return isEmpty(v.selected);
    case "text":
      return isEmpty(v.text?.trim());
    case "thumbs_up_down":
      return !v.value;
    case "numeric":
      return isNil(v.value);
    default:
      return false;
  }
};

const ANSWER_KEYS = {
  text: "text",
  numeric: "value",
  star: "rating",
  categorical: "selected",
  thumbs_up_down: "value",
};

// The answer inside a stored or field value: answers may be kept bare
// ("a note", 8, "positive") or in their field shape ({ text }, { value }, ...).
const answerOf = (type, v) => {
  const key = ANSWER_KEYS[type];
  if (key && v !== null && typeof v === "object" && !Array.isArray(v))
    return v[key];
  return v;
};

const asNumber = (v) => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

/**
 * Whether two values of a label are the same answer, compared the way the
 * server compares a submitted answer with the stored one: numbers and stars
 * by value, choices as the same options in any order, text and thumbs
 * exactly. A missing answer on either side is never the same.
 */
export const isSameLabelValue = (type, a, b) => {
  const x = answerOf(type, a);
  const y = answerOf(type, b);
  if (isNil(x) || isNil(y)) return false;
  switch (type) {
    case "numeric":
    case "star": {
      const m = asNumber(x);
      const n = asNumber(y);
      return m !== null && n !== null && m === n;
    }
    case "categorical": {
      const xs = (Array.isArray(x) ? x : [x]).map(String).sort();
      const ys = (Array.isArray(y) ? y : [y]).map(String).sort();
      return xs.length === ys.length && xs.every((s, i) => s === ys[i]);
    }
    default:
      return x === y;
  }
};
