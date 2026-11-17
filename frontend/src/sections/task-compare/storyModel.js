// Side by side · plain-language step text (pure). Beats from the aligned
// lane steps (templates), or the mock's hand-authored beats (T26 / T1 / T20)
// which win for their lane. Rows = alignment rows grouped by meaning
// (request · understanding · lookup · action · reply · end); a lane with
// nothing in a row shows a quiet gap sentence (`Not in #2B: never looked up
// the order`). No React; tested in __tests__/storyModel.test.js.
import { fmtCost, fmtNumber } from "src/sections/common/format/fmt";

const READ_TOOL = /^(lookup_|search_|get_|list_|find_|read_)/;
const ASK_RE =
  /\?\s*$|could you|can you (double-)?check|please (confirm|check)/i;
const ID_RE = /order (number|id)|double-check/i;

export const GROUP_LABEL = Object.freeze({
  request: "Request",
  lookup: "Looked up",
  action: "Did",
  reply: "Told the customer",
  end: "Ended",
  other: "Other",
});

const parse = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};
const str = (v) =>
  typeof v === "string" ? v : v == null ? "" : JSON.stringify(v);
const words = (t) => str(t).split(/\s+/).filter(Boolean).length;
const quote = (t, max = 120) => {
  const s = str(t).replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};
const firstVal = (input) => {
  const v = parse(input);
  if (v && typeof v === "object" && !Array.isArray(v))
    return v[Object.keys(v)[0]];
  return v;
};
const toolOf = (s) => s?.toolName ?? s?.name ?? "";

/** The meaning group of a lane step. */
export function groupOf(step) {
  if (!step) return "other";
  if (step.kind === "customer") return "request";
  if (step.kind === "reply") return "reply";
  if (step.kind === "end") return "end";
  if (step.kind === "retrieval") return "lookup";
  if (step.kind === "tool")
    return READ_TOOL.test(toolOf(step)) ? "lookup" : "action";
  if (["file_edit", "command", "test", "subagent"].includes(step.kind))
    return "action";
  return "other";
}

/** Template beat text for an internal model step (null = not story-worthy). */
export function thinkBeat(step) {
  const out = parse(step?.output) ?? {};
  const name = step?.name ?? "";
  if (/classify|router|extract/i.test(name)) {
    const id = out.order_ref ?? out.order_id ?? null;
    if (id) return `Read the order id as "${id}"`;
    if (out.intent === "multi_request" || out.route === "multi_request")
      return "Read the request as more than one ask";
    if (out.route || out.intent)
      return `Read the request as ${String(out.route ?? out.intent).replace(/_/g, " ")}`;
    return null;
  }
  if (/reformulate|retry/i.test(name)) {
    const id = out.order_ref ?? out.order_id ?? null;
    return id ? `Tried again with "${id}"` : "Tried again";
  }
  if (/policy/i.test(name)) {
    if (out.rule) return `Checked the policy: ${quote(out.rule, 90)}`;
    if (out.allowed != null)
      return out.allowed
        ? "Checked the policy: allowed"
        : "Checked the policy: not allowed";
    return "Checked the policy";
  }
  if (/plan/i.test(name)) {
    const a =
      out.action ??
      (Array.isArray(out.actions) ? out.actions.join(" and ") : null);
    if (a === "ask_customer") return "Decided to ask the customer";
    return a ? `Decided to ${String(a).replace(/_/g, " ")}` : null;
  }
  return null;
}

/** Refund amounts as the world writes them (`$149`); costs use fmtCost. */
const amount = (v) => (Number.isFinite(v) ? `$${fmtNumber(v)}` : null);

/** Template beat text for a milestone step. */
export function milestoneBeat(step, { firstCustomer = true } = {}) {
  const out = parse(step?.output);
  const tool = toolOf(step);
  switch (step?.kind) {
    case "customer":
      return firstCustomer
        ? `Customer: "${quote(step.output, 140)}"`
        : `Customer replied: "${quote(step.output, 90)}"`;
    case "reply": {
      const text = str(step.output);
      if (ASK_RE.test(text) && ID_RE.test(text))
        return "Asked the customer for the order id again";
      if (ASK_RE.test(text) && words(text) < 30)
        return `Asked the customer: "${quote(text, 90)}"`;
      return `Told the customer: "${quote(text, 160)}"`;
    }
    case "end": {
      const changes = out?.changes ?? [];
      if (!changes.length) return "Ended with no changes";
      const plain = changes
        .slice(0, 2)
        .map((c) => String(c?.text ?? c?.path ?? "").replace(/\s*[{[].*$/, ""))
        .filter(Boolean);
      return `Ended with ${changes.length} ${changes.length === 1 ? "change" : "changes"}${plain.length ? `: ${plain.join(", ")}${changes.length > 2 ? ", …" : ""}` : ""}`;
    }
    case "retrieval": {
      const q = firstVal(step.input);
      const n = Array.isArray(out) ? out.length : null;
      return `Searched the help center${q ? ` for "${quote(q, 50)}"` : ""}${n != null ? ` — ${n} ${n === 1 ? "article" : "articles"}` : ""}`;
    }
    case "file_edit": {
      const o = out ?? {};
      return `Edited ${o.path ?? step.input?.path ?? "a file"}${o.added != null ? ` (+${o.added} −${o.removed ?? 0})` : ""}`;
    }
    case "command":
    case "test": {
      const cmd = str(step.input?.command ?? step.input ?? step.name).split(
        " ",
      )[0];
      const passed = out?.passed ?? null;
      return `Ran ${cmd}${passed != null ? ` — ${passed} passed` : ""}${step.error ? " — failed" : ""}`;
    }
    case "tool":
      break;
    default:
      return step?.name ? `Ran ${step.name}` : null;
  }
  const arg = firstVal(step.input);
  if (step.error) {
    const code = out?.error?.code ?? /^(\d{3})/.exec(String(step.error))?.[1];
    if (/lookup_order/.test(tool))
      return `Looked up order ${arg} — not found${code ? ` (${code})` : ""}`;
    return `${tool} failed${code ? ` (${code})` : ""}`;
  }
  if (/lookup_order/.test(tool)) {
    if (out?.in_stock != null)
      return `Checked replacement stock: ${out.in_stock ? `${out.in_stock} in stock` : `none, restock ${out.restock ?? "later"}`}`;
    const status = out?.status ? ` (${out.status})` : "";
    return `Looked up order ${arg} — found${status}`;
  }
  if (/issue_refund|refund/.test(tool)) {
    const amt = amount(parse(step.input)?.amount);
    const id = out?.refund_id ? ` (${out.refund_id})` : "";
    return `Refunded ${amt ?? "the order"}${id}`;
  }
  if (/search_kb|search/.test(tool)) {
    const n = Array.isArray(out) ? out.length : null;
    return `Searched the help center for "${quote(arg, 50)}"${n != null ? ` — ${n} ${n === 1 ? "article" : "articles"}` : ""}`;
  }
  if (/lookup_customer/.test(tool))
    return `Looked up the customer${out?.payment_method ? ` — ${out.payment_method}` : ""}`;
  if (/create_ticket/.test(tool))
    return `Opened a ticket${out?.ticket ? ` (${out.ticket})` : ""}`;
  if (/escalate/.test(tool))
    return `Escalated to a person${arg ? ` (${arg})` : ""}`;
  if (/send_email/.test(tool)) return "Sent the customer an email";
  if (/cancel/.test(tool)) return `Cancelled ${arg ?? "an item"}`;
  if (/address/.test(tool)) return "Changed the delivery address";
  if (/callback/.test(tool)) return "Booked a callback";
  return `Called ${tool}`;
}

/** Interim harness chatter (`Order found. Reading policy.`) stays out of Story. */
export const isChatter = (step, isLastReply) =>
  step?.kind === "reply" &&
  !isLastReply &&
  words(step.output) < 8 &&
  !ASK_RE.test(str(step.output));

/** Quiet gap sentence for a lane that has nothing in a row. */
export function gapSentence(laneLabel, otherStep) {
  const tool = toolOf(otherStep);
  let what = "did not do this";
  if (otherStep?.kind === "customer")
    return "Started from the customer's message (no chat turn)";
  else if (otherStep?.kind === "reply") what = "no reply here";
  else if (/lookup_order/.test(tool)) what = "never looked up the order";
  else if (/lookup_customer/.test(tool)) what = "never looked up the customer";
  else if (/search|retriev/.test(tool) || otherStep?.kind === "retrieval")
    what = "never searched the help center";
  else if (/refund/.test(tool)) what = "never issued a refund";
  else if (/create_ticket/.test(tool)) what = "never opened a ticket";
  else if (tool) what = `never called ${tool}`;
  return `Not in ${laneLabel}: ${what}`;
}

/** Steps (milestone + riding think steps) of one cell, in step order. */
function cellSteps(cell) {
  if (!cell) return [];
  if (cell.type === "gap") return cell.steps ?? [];
  return [...(cell.subSteps ?? []), cell.step].filter(Boolean);
}

/**
 * Story rows for an alignment.
 * @param {{rows, lanes:[{id, label, story?: [{text, step_n, tone}], checks?}],
 *   firstWrong?: {laneId, stepN}}} args  lanes base first (same as cells)
 * @returns {{id, group, label, rowIds, cells:[{beats:[{text, stepN, tone,
 *   firstWrong, failed:[]}], gap: string|null}]}[]}
 */
export function storyRows({ rows = [], lanes = [], firstWrong = null } = {}) {
  const lastReplyN = lanes.map((_, l) => {
    let n = null;
    rows.forEach((r) =>
      cellSteps(r.cells[l]).forEach((s) => {
        if (s.kind === "reply") n = s.n;
      }),
    );
    return n;
  });
  const seenCustomer = lanes.map(() => false);
  const failedByLane = lanes.map((lane) =>
    (lane.checks ?? []).filter(
      (c) => c.passed === false && Number.isFinite(c.cause_step_n),
    ),
  );
  const built = rows.map((row) => {
    const firstStep =
      row.cells
        .map(cellSteps)
        .flat()
        .find((s) => s?.isMilestone !== false) ?? null;
    const group = groupOf(firstStep);
    const cells = row.cells.map((cell, l) => {
      const lane = lanes[l];
      const steps = cellSteps(cell);
      let beats;
      if (lane?.story?.length) {
        const ns = new Set(steps.map((s) => s.n));
        beats = lane.story
          .filter((b) => ns.has(b.step_n))
          .map((b) => ({
            text: b.text,
            stepN: b.step_n,
            tone: b.tone ?? "neutral",
          }));
      } else {
        beats = steps
          .map((s) => {
            if (s.kind === "think") {
              const text = thinkBeat(s);
              return text ? { text, stepN: s.n, tone: "neutral" } : null;
            }
            if (isChatter(s, s.n === lastReplyN[l])) return null;
            const text = milestoneBeat(s, { firstCustomer: !seenCustomer[l] });
            if (s.kind === "customer") seenCustomer[l] = true;
            return text
              ? { text, stepN: s.n, tone: s.error ? "bad" : "neutral" }
              : null;
          })
          .filter(Boolean);
      }
      return { beats, steps, gap: null };
    });
    return { id: row.id, rowIds: [row.id], group, firstStep, cells };
  });

  // Mark the first wrong step and failed checks on their beats.
  built.forEach((r) =>
    r.cells.forEach((c, l) => {
      c.beats.forEach((b) => {
        b.firstWrong = Boolean(
          firstWrong &&
            firstWrong.laneId === lanes[l]?.id &&
            firstWrong.stepN === b.stepN,
        );
        b.failed = [];
      });
    }),
  );
  failedByLane.forEach((checks, l) =>
    checks.forEach((chk) => {
      const all = built.flatMap((r) => r.cells[l].beats);
      const exact = all.find((b) => b.stepN === chk.cause_step_n);
      const before = [...all]
        .reverse()
        .find((b) => b.stepN <= chk.cause_step_n);
      const target = exact ?? before ?? all[all.length - 1];
      if (target) target.failed.push(chk.key);
    }),
  );

  // Merge consecutive rows of the same group; drop rows with no beats.
  const merged = [];
  built.forEach((r) => {
    const prev = merged[merged.length - 1];
    if (prev && prev.group === r.group && r.group !== "other") {
      prev.rowIds.push(r.id);
      r.cells.forEach((c, l) => {
        prev.cells[l].beats.push(...c.beats);
        prev.cells[l].steps.push(...c.steps);
      });
    } else
      merged.push({
        ...r,
        cells: r.cells.map((c) => ({
          ...c,
          beats: [...c.beats],
          steps: [...c.steps],
        })),
      });
  });
  return merged
    .filter((r) => r.cells.some((c) => c.beats.length))
    .map((r) => {
      const other =
        r.cells.flatMap((c) => c.steps).find((s) => s.kind !== "think") ??
        r.firstStep;
      return {
        id: r.id,
        rowIds: r.rowIds,
        group: r.group,
        label: GROUP_LABEL[r.group] ?? "Other",
        cells: r.cells.map((c, l) => ({
          beats: c.beats,
          gap: c.beats.length
            ? null
            : c.steps.length
              ? null
              : gapSentence(lanes[l]?.label ?? "this run", other),
          quiet: !c.beats.length && c.steps.length > 0,
        })),
      };
    });
}

/**
 * Checks as sentences: `✓ Refund issued once` · `✕ Order found — lookup_order
 * returned 404 for "8830"` · `– Refund amount: not checked`.
 */
export function checkSentences(checks, rowChecks = []) {
  const nameOf = (k) => rowChecks.find((c) => c.key === k)?.name ?? k;
  return (checks ?? []).map((c) => {
    const name = nameOf(c.key);
    if (c.passed === true)
      return { key: c.key, tone: "pass", text: `✓ ${name}` };
    if (c.passed === false)
      return {
        key: c.key,
        tone: "fail",
        text: `✕ ${name}${c.evidence ? ` — ${c.evidence}` : ""}`,
        stepN: c.cause_step_n ?? null,
      };
    return {
      key: c.key,
      tone: "none",
      text: `– ${name}: not checked`,
      tooltip: "Not reached: an earlier check failed.",
    };
  });
}

/** `$0.011 · 7 steps` style summary for a story header (cost may be null). */
export const storyCost = (t) =>
  Number.isFinite(t?.cost_usd) ? fmtCost(t.cost_usd) : null;
