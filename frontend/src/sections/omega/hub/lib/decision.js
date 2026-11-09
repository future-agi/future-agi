// The decision rules of SPEC §5.4, as the review form applies them before the
// server does: approve needs every flag acknowledged, every checklist item of
// the current rules ticked, and a confirmed disclosure with no observed gap.
import { disclosureGaps, disclosureShapeErrors } from "./disclosure";
import { REASON_MAX, REASON_MIN } from "../constants";

export function approveBlockers({ flags, acknowledged, checklistItems, checklist, disclosure, observed }) {
  const blockers = [];
  const unacked = (flags || []).filter((id) => !(acknowledged || []).includes(id));
  if (unacked.length) blockers.push(`acknowledge ${unacked.length} flag${unacked.length === 1 ? "" : "s"}`);
  const unticked = (checklistItems || []).filter((item) => checklist?.[item.key] !== true);
  if (!checklistItems?.length) blockers.push("load the current checklist");
  else if (unticked.length) blockers.push(`tick ${unticked.length} checklist item${unticked.length === 1 ? "" : "s"}`);
  const gaps = disclosureGaps(disclosure, observed);
  if (gaps.length) blockers.push(`say what it does under ${gaps.join(", ")}`);
  const shape = Object.keys(disclosureShapeErrors(disclosure));
  if (shape.length && !gaps.length) blockers.push(`complete the disclosure (${shape.join(", ")})`);
  return blockers;
}

export function reasonProblem(reason) {
  const length = String(reason || "").trim().length;
  if (length < REASON_MIN) return `at least ${REASON_MIN} characters (${length} so far)`;
  if (length > REASON_MAX) return `at most ${REASON_MAX} characters`;
  return null;
}

export function decisionPayload({ decision, rulesVersion, reason, checklistItems, acknowledged, disclosure, notes }) {
  if (decision === "approve") {
    return {
      decision,
      rulesVersion,
      checklist: Object.fromEntries((checklistItems || []).map((item) => [item.key, true])),
      acknowledgedFlags: [...(acknowledged || [])].sort(),
      disclosure: Object.fromEntries(Object.entries(disclosure).map(([k, v]) => [k, String(v || "").trim()])),
    };
  }
  const payload = { decision, rulesVersion, reason: String(reason || "").trim() };
  if (decision === "request_changes" && notes?.length) payload.notes = notes;
  return payload;
}
