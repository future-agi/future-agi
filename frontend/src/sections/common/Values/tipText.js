// The hover text of a grid cell, formatted for its value: an object, an
// array or a JSON string reads as indented JSON (two spaces), any other
// text as it is. An array of plain numbers (an embedding, a score list)
// stays on one line, so a long vector is not a tall column of numbers.
// Capped at `max` characters. Pure; never throws.

export const TIP_MAX = 2000;

const parsedJson = (s) => {
  const t = s.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return undefined;
  try {
    const v = JSON.parse(t);
    return v !== null && typeof v === "object" ? v : undefined;
  } catch {
    return undefined;
  }
};

const isPlainNumberList = (v) =>
  v.every((x) => x === null || typeof x === "number");

// JSON.stringify(v, null, 2) except that number lists stay inline.
function pretty(value, indent) {
  const v =
    value && typeof value.toJSON === "function" ? value.toJSON() : value;
  if (Array.isArray(v)) {
    if (!v.length || isPlainNumberList(v)) return JSON.stringify(v);
    const inner = `${indent}  `;
    const items = v.map((x) => {
      const s = pretty(x, inner);
      return `${inner}${s === undefined ? "null" : s}`;
    });
    return `[\n${items.join(",\n")}\n${indent}]`;
  }
  if (v !== null && typeof v === "object") {
    const inner = `${indent}  `;
    const entries = Object.keys(v)
      .map((k) => [k, pretty(v[k], inner)])
      .filter(([, s]) => s !== undefined);
    if (!entries.length) return "{}";
    return `{\n${entries
      .map(([k, s]) => `${inner}${JSON.stringify(k)}: ${s}`)
      .join(",\n")}\n${indent}}`;
  }
  return JSON.stringify(v);
}

export function tipText(value, max = TIP_MAX) {
  let s;
  if (typeof value === "string") {
    const v = parsedJson(value);
    s = v === undefined ? value : pretty(v, "");
  } else {
    try {
      s = pretty(value, "") ?? String(value);
    } catch {
      s = String(value);
    }
  }
  return s.length > max ? `${s.slice(0, max)}…` : s;
}
