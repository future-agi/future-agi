// W5-1 · value rules of the evaluator (W5-SCOPE §5 A0): equality, ordering,
// contains, emptiness, the functions and arithmetic. Pure.
export const WORD = Symbol("word");
export const word = (v) => ({ [WORD]: v });
export const isWord = (v) => v != null && typeof v === "object" && WORD in v;

export const isMissing = (v) =>
  v === undefined ||
  v === null ||
  (typeof v === "number" && !Number.isFinite(v));

/** `is empty`: missing, "", [] or {}. */
export function isEmptyValue(v) {
  if (isMissing(v)) return true;
  if (v === "") return true;
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v).length === 0;
  return false;
}

export const asNumber = (v) => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "boolean") return null;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)))
    return Number(v);
  return null;
};
export const asText = (v) =>
  typeof v === "string"
    ? v
    : typeof v === "number" || typeof v === "boolean"
      ? String(v)
      : JSON.stringify(v);

/** = on two present values (lists: membership; numbers: numeric; text: exact). */
export function equal(a, b) {
  if (isWord(b)) return typeof a === "string" && a.toLowerCase() === b[WORD];
  if (isWord(a)) return equal(b, a);
  if (Array.isArray(a) && !Array.isArray(b))
    return a.some((x) => !isMissing(x) && equal(x, b));
  if (Array.isArray(b) && !Array.isArray(a)) return equal(b, a);
  if (typeof a === "boolean" || typeof b === "boolean") {
    if (typeof a === "boolean" && typeof b === "boolean") return a === b;
    return asText(a).toLowerCase() === asText(b).toLowerCase();
  }
  const na = asNumber(a);
  const nb = asNumber(b);
  if (
    na !== null &&
    nb !== null &&
    (typeof a === "number" || typeof b === "number")
  )
    return na === nb;
  if (typeof a === "object" || typeof b === "object")
    return asText(a) === asText(b);
  return a === b;
}

export function order(op, a, b) {
  if (isWord(a) || isWord(b)) return false;
  const na = asNumber(a);
  const nb = asNumber(b);
  let x;
  let y;
  if (na !== null && nb !== null) [x, y] = [na, nb];
  else if (typeof a === "string" && typeof b === "string") [x, y] = [a, b];
  else return false;
  switch (op) {
    case "<":
      return x < y;
    case "<=":
      return x <= y;
    case ">":
      return x > y;
    case ">=":
      return x >= y;
    default:
      return false;
  }
}

export function containsValue(hay, needle) {
  if (isMissing(hay) || isMissing(needle)) return false;
  const n = isWord(needle) ? needle[WORD] : asText(needle).toLowerCase();
  if (Array.isArray(hay))
    return hay.some((x) => !isMissing(x) && asText(x).toLowerCase() === n);
  return asText(hay).toLowerCase().includes(n);
}

export const FUNCS = {
  len: ([v]) =>
    isMissing(v)
      ? null
      : Array.isArray(v)
        ? v.length
        : typeof v === "object"
          ? Object.keys(v).length
          : asText(v).length,
  lower: ([v]) => (isMissing(v) ? null : asText(v).toLowerCase()),
  upper: ([v]) => (isMissing(v) ? null : asText(v).toUpperCase()),
  abs: ([v]) => (asNumber(v) === null ? null : Math.abs(asNumber(v))),
  round: ([v, d]) => {
    const n = asNumber(v);
    if (n === null) return null;
    const places = asNumber(d) ?? 0;
    const f = 10 ** Math.max(0, Math.min(12, Math.trunc(places)));
    return Math.round(n * f) / f;
  },
  min: (args) => {
    const ns = args.map(asNumber).filter((n) => n !== null);
    return ns.length ? Math.min(...ns) : null;
  },
  max: (args) => {
    const ns = args.map(asNumber).filter((n) => n !== null);
    return ns.length ? Math.max(...ns) : null;
  },
  coalesce: (args) => args.find((v) => !isEmptyValue(v)) ?? null,
};

export function arith(op, a, b) {
  if (op === "+" && typeof a === "string" && typeof b === "string")
    return a + b;
  const x = asNumber(a);
  const y = asNumber(b);
  if (x === null || y === null) return null;
  switch (op) {
    case "+":
      return x + y;
    case "-":
      return x - y;
    case "*":
      return x * y;
    case "/":
      return y === 0 ? null : x / y;
    default:
      return null;
  }
}
