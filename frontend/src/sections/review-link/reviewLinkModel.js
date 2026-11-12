// The outside reviewer's token page (W4-SCOPE §8 C3): pure helpers the page
// and the mock server share (an answer is complete, the keyboard keys, the
// expiry words). No React, no I/O.
import {
  labelProblems,
  problemsText,
} from "src/sections/annotations/queues/utils/label-rules";

/** True when `value` is a real answer for `label` (label-input shapes). */
export function isAnswered(label, value) {
  if (!label || value == null || typeof value !== "object") return false;
  switch (label.type) {
    case "star":
      return Number(value.rating) > 0;
    case "categorical":
      return Array.isArray(value.selected) && value.selected.length > 0;
    case "numeric":
      return value.value != null && Number.isFinite(Number(value.value));
    case "text":
      return String(value.text ?? "").trim().length > 0;
    case "thumbs_up_down":
      return value.value === "up" || value.value === "down";
    default:
      return false;
  }
}

/**
 * Check a submission against the invite's label definitions: every label
 * answered, no other label ids, then the one review rule every other review
 * door uses (KH B1, label-rules.js: range, step, stars, options, length; no
 * clamp). Returns null or the problem sentence.
 */
export function answerProblem(labels, values) {
  const defs = new Map((labels ?? []).map((l) => [l.id, l]));
  const given = new Map((values ?? []).map((v) => [v?.label_id, v?.value]));
  const unknown = [...given.keys()].find((id) => !defs.has(id));
  if (unknown) return "That label isn't part of this review.";
  const missing = (labels ?? []).find((l) => !isAnswered(l, given.get(l.id)));
  if (missing) return `Answer “${missing.name}” first.`;
  const problems = labelProblems(labels, Object.fromEntries(given));
  return problems.length ? problemsText(problems) : null;
}

/**
 * The value key `n` (1-9) picks on the first label, when that label has
 * discrete choices: categorical options in order, thumbs up / down, stars.
 */
export function valueForKey(label, n) {
  const i = Number(n);
  if (!label || !Number.isInteger(i) || i < 1 || i > 9) return null;
  if (label.type === "categorical") {
    const opt = (label.settings?.options ?? [])[i - 1];
    if (!opt) return null;
    return {
      selected: [typeof opt === "string" ? opt : opt.label ?? opt.value],
    };
  }
  if (label.type === "thumbs_up_down")
    return i === 1 ? { value: "up" } : i === 2 ? { value: "down" } : null;
  if (label.type === "star") {
    const max = label.settings?.no_of_stars ?? null;
    return max && i <= max ? { rating: i } : null;
  }
  return null;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** `Wed 29 Oct` (local time). */
export function dayWords(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** `Invited by Maya Chen · 6 items · expires Wed 29 Oct`. */
export function inviteLine({ invitedBy, total, expiresAt }) {
  const parts = [];
  if (invitedBy) parts.push(`Invited by ${invitedBy}`);
  parts.push(`${total} ${total === 1 ? "item" : "items"}`);
  if (expiresAt) parts.push(`expires ${dayWords(expiresAt)}`);
  return parts.join(" · ");
}

/** The done screen: `Thanks, you reviewed 6 items. You can close this tab.` */
export const doneLine = (n) =>
  `Thanks, you reviewed ${n} ${n === 1 ? "item" : "items"}. You can close this tab.`;

/** The token-page error copy for a resolve / item status. */
export function errorCopy(status, message) {
  if (status === 404) return message || "Link not found";
  if (status === 410) return "Link expired";
  if (status === 403)
    return message || "This review link is waiting for an admin to approve it.";
  return message || "Something went wrong. Reload the page.";
}

/** A field value as editable text (the Correct editor and its diff). */
export const asText = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2);

/** The pairwise choice label (blind Response 1 / Tie / Response 2). */
export const isPairLabel = (l) => l?.settings?.kind === "pairwise";

/**
 * What key `k` (already lower-cased) does on the token page: `{move: ±1}`
 * for J / K, `{value}` for 1-9 on the first label (pairwise: 1 = Response 1,
 * 2 = Tie, 3 = Response 2, stored as that response's side), else null.
 */
export function keyAction(k, firstLabel, candidates) {
  if (k === "j") return { move: 1 };
  if (k === "k") return { move: -1 };
  if (!firstLabel || !/^[1-9]$/.test(k)) return null;
  if (isPairLabel(firstLabel)) {
    const sideOf = (slot) =>
      (candidates ?? []).find((c) => c.slot === slot)?.side ?? null;
    const stored =
      k === "1" ? sideOf(1) : k === "2" ? "Tie" : k === "3" ? sideOf(2) : null;
    return stored ? { value: { selected: [stored] } } : null;
  }
  const value = valueForKey(firstLabel, k);
  return value ? { value } : null;
}
