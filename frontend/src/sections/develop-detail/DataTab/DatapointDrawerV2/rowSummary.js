// The Row view's one-line summary (IT-R1 G5 P10): what is in this row at a
// glance — `tier enterprise · 3 tools used · 2 images · 1 audio · 1 PDF` —
// before scrolling through cards. Pure.

const parse = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!/^[[{]/.test(t)) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

const toolName = (c) => c?.function?.name ?? c?.name ?? c?.tool ?? null;

function collectTools(v, out, depth = 0, calls = { n: 0 }) {
  if (!v || typeof v !== "object" || depth > 6) return calls;
  if (Array.isArray(v)) {
    v.forEach((x) => collectTools(x, out, depth + 1, calls));
    return calls;
  }
  (Array.isArray(v.tool_calls) ? v.tool_calls : []).forEach((c) => {
    const n = toolName(c);
    if (n) {
      out.add(n);
      calls.n += 1;
    }
  });
  if (v.role === "tool" && (v.name || v.tool_name))
    out.add(v.name ?? v.tool_name);
  Object.entries(v).forEach(([k, x]) => {
    if (k !== "tool_calls" && x && typeof x === "object")
      collectTools(x, out, depth + 1, calls);
  });
  return calls;
}

const tierOf = (v) => {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const t = v.customer?.tier ?? v.tier ?? null;
  return typeof t === "string" || typeof t === "number" ? String(t) : null;
};

const n = (k, one, many = `${one}s`) => `${k} ${k === 1 ? one : many}`;

/** @param {{input, expected, outputs, metadata, noRole}} model buildRowModel's */
export function rowSummaryParts(model) {
  const fields = [
    ...(model?.input ?? []),
    ...(model?.expected ?? []),
    ...(model?.metadata ?? []),
    ...(model?.noRole ?? []),
  ];
  let images = 0;
  let audio = 0;
  let video = 0;
  let pdf = 0;
  let tier = null;
  const tools = new Set();
  let calls = 0;
  fields.forEach((f) => {
    const raw = f?.cell?.cell_value;
    if (raw == null || raw === "") return;
    const type = String(f.column?.data_type ?? "").toLowerCase();
    const v = parse(raw);
    if (type === "image") images += 1;
    else if (type === "images") images += Array.isArray(v) ? v.length : 1;
    else if (type === "audio") audio += 1;
    else if (type === "video") video += 1;
    else if (
      type === "file" ||
      type === "document" ||
      (typeof raw === "string" && /\.pdf(\?|#|$)/i.test(raw.trim()))
    )
      pdf += 1;
    calls += collectTools(v, tools).n;
    tier = tier ?? tierOf(v);
  });
  return [
    tier && `tier ${tier}`,
    // IT-R2 G5 P6: what the row holds, counted as calls (`1 tool call`).
    (calls || tools.size) && n(calls || tools.size, "tool call"),
    images && n(images, "image"),
    audio && `${audio} audio`,
    video && n(video, "video"),
    pdf && n(pdf, "PDF"),
  ].filter(Boolean);
}
