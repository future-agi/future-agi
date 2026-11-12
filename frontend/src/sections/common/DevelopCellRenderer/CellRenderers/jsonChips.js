// Simple-mode JSON summaries (SVP-4.2, DG#26): tool calls read
// `lookup_order(order_id) +1`, objects `{ 1 order, 1 customer }`. Pure.
const parse = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return undefined;
  }
};

const callName = (c) => c?.name ?? c?.tool ?? c?.function?.name ?? null;
const callArgs = (c) => {
  const a = c?.args ?? c?.arguments ?? c?.function?.arguments ?? null;
  const obj = typeof a === "string" ? parse(a) : a;
  return obj && typeof obj === "object" ? Object.keys(obj) : [];
};

/** `{text, more, count, kind}` or null when the value isn't summarisable. */
export function jsonChipSummary(value) {
  const v = parse(value);
  if (v === undefined || v === null) return null;
  if (Array.isArray(v)) {
    if (!v.length) return null;
    if (v.every((c) => c && typeof c === "object" && callName(c))) {
      const first = v[0];
      const args = callArgs(first);
      return {
        kind: "calls",
        text: `${callName(first)}(${args.slice(0, 2).join(", ")}${args.length > 2 ? ", …" : ""})`,
        more: v.length - 1,
        count: v.length,
      };
    }
    return null;
  }
  if (typeof v === "object") {
    // IT-R1 G5 P5: an imported conversation (`{turns: [...], resolution, …}`)
    // reads as the same turn chips as older rows (`8 turns · user ·
    // assistant`), not `{ 8 turns, resolution, … }`: GridValueSummary owns it.
    if (Array.isArray(v.turns) || Array.isArray(v.messages)) return null;
    const keys = Object.keys(v);
    // Flat objects keep the R2 typed summary (`{2 keys}` + key chips); the
    // chip line is for nested ones (`{ 1 order, 1 customer }`).
    if (!keys.length || !keys.some((k) => v[k] && typeof v[k] === "object"))
      return null;
    const parts = keys.slice(0, 3).map((k) => {
      const x = v[k];
      const n = Array.isArray(x)
        ? x.length
        : x && typeof x === "object"
          ? 1
          : null;
      return n == null ? k : `${n} ${k}`;
    });
    return {
      kind: "object",
      text: `{ ${parts.join(", ")}${keys.length > 3 ? ", …" : ""} }`,
      more: 0,
      count: keys.length,
    };
  }
  return null;
}
