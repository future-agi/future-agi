// Outside reviewers by link (W4-SCOPE §8 C3; MUST-BUILD W4-4). Pure helpers
// the Start review form, the review summary and the mock server share, so
// the sample size, the split, the filters and the status words have one
// definition. No React, no I/O, no clock reads (callers pass `now`).

/** The status words of an invite (the summary table). */
export const INVITE_STATUS_LABELS = Object.freeze({
  pending_approval: "Waiting for approval",
  active: "Active",
  done: "Done",
  revoked: "Revoked",
  expired: "Expired",
});

export const SAMPLE_MODES = Object.freeze(["all", "percent", "count"]);
export const SAMPLE_FILTERS = Object.freeze(["all", "failing", "disagree"]);

/**
 * How many items a sample takes from `n`: all of them; round(n × p / 100)
 * (at least 1); or the first `count` (at most n). 0 when n is 0.
 */
export function sampleSize(n, { mode = "all", value } = {}) {
  const total = Math.max(0, Math.floor(Number(n) || 0));
  if (!total) return 0;
  if (mode === "percent") {
    const p = Math.min(100, Math.max(0, Number(value) || 0));
    return Math.min(total, Math.max(1, Math.round((total * p) / 100)));
  }
  if (mode === "count") {
    const c = Math.floor(Number(value) || 0);
    return Math.min(total, Math.max(1, c));
  }
  return total;
}

/** `ids` split in order into `parts` shares, as even as possible. */
export function splitInOrder(ids, parts) {
  const list = [...(ids ?? [])];
  const k = Math.max(1, Math.floor(Number(parts) || 1));
  const base = Math.floor(list.length / k);
  const extra = list.length % k;
  const out = [];
  let at = 0;
  for (let i = 0; i < k; i += 1) {
    const size = base + (i < extra ? 1 : 0);
    out.push(list.slice(at, at + size));
    at += size;
  }
  return out;
}

/**
 * The R3 pills a sample's `from` filter means, over the queue's runs:
 * failing = the candidate run fails the metric; disagree (pairwise only) =
 * the metric moved between the two runs (one passes where the other fails).
 * `[]` for all items, null when the filter does not apply.
 */
export function samplePills(filter, { mode, metric, runId, runA, runB } = {}) {
  if (!filter || filter === "all") return [];
  if (!metric) return null;
  const candidate = mode === "pairwise" ? runB : runId;
  if (filter === "failing")
    return candidate ? [{ type: "fails", metric, run: candidate }] : null;
  if (filter === "disagree")
    return mode === "pairwise" && runA && runB
      ? [{ type: "movement", dir: "changed", metric, run: runB }]
      : null;
  return null;
}

/** `Failing on #4A` · `Where #4A and #3A disagree` · `All items`. */
export function filterLabel(filter, { mode, labelA, labelB, label } = {}) {
  if (filter === "failing")
    return `Failing on ${mode === "pairwise" ? labelB : label}`;
  if (filter === "disagree") return `Where ${labelB} and ${labelA} disagree`;
  return "All items";
}

/**
 * An invite's status at `now`: revoked and expired win over the rest; an
 * invite nobody approved waits; one with every item answered is done.
 */
export function inviteStatus(invite, now) {
  if (!invite) return null;
  if (invite.revoked_at) return "revoked";
  const at = Number.isFinite(now) ? now : Date.parse(now);
  if (invite.expires_at && Date.parse(invite.expires_at) <= at)
    return "expired";
  if (invite.needs_approval && !invite.approved_by) return "pending_approval";
  const total = invite.item_ids?.length ?? 0;
  const done = invite.progress?.done ?? 0;
  if (total > 0 && done >= total) return "done";
  return "active";
}

const EMAIL_RE = /^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/;

/** True for one plausible email address. */
export const isEmail = (text) => EMAIL_RE.test(String(text ?? "").trim());

/**
 * Split typed or pasted text into addresses (commas, semicolons, spaces,
 * new lines; `Name <a@b.c>` keeps the address), lower-cased, deduplicated.
 * Returns `{emails, invalid}`.
 */
export function parseEmails(text) {
  const parts = String(text ?? "")
    .split(/[\s,;]+/)
    .map((p) => p.replace(/^<|>$/g, "").trim())
    .filter(Boolean);
  const emails = [];
  const invalid = [];
  parts.forEach((p) => {
    const e = p.toLowerCase();
    if (!isEmail(e)) invalid.push(p);
    else if (!emails.includes(e)) emails.push(e);
  });
  return { emails, invalid };
}

/** `1 day` · `7 days`. */
export const daysLabel = (n) => `${n} ${Number(n) === 1 ? "day" : "days"}`;

/** `A random 20% (6 items)` / `A random 6 items` / `All 30 items`. */
export function sampleLabel(n, sample) {
  const k = sampleSize(n, sample);
  const items = (x) => `${x} ${x === 1 ? "item" : "items"}`;
  if (sample?.mode === "percent")
    return `A random ${Number(sample.value) || 0}% (${items(k)})`;
  if (sample?.mode === "count") return `A random ${items(k)}`;
  return `All ${items(n)}`;
}

/** Items each reviewer gets: the whole sample, or their own share. */
export function perReviewerCounts(k, reviewers, split) {
  const r = Math.max(0, Math.floor(Number(reviewers) || 0));
  if (!r) return [];
  if (split !== "split") return Array.from({ length: r }, () => k);
  return splitInOrder(
    Array.from({ length: k }, (_, i) => i),
    r,
  ).map((part) => part.length);
}

/** `k of K` progress words. */
export const progressWords = (p) => `${p?.done ?? 0} of ${p?.total ?? 0}`;

// ------------------------------------------------------- the invite form
/** The sample of the form (`{mode, value}`). */
export const formSample = (v) => ({
  mode: v.mode,
  value: v.mode === "percent" ? v.percent : v.count,
});

/** The form's starting value from the server's policy. */
export function initialInvite(policy) {
  const d = policy?.defaults ?? {};
  return {
    emails: [],
    mode: "all",
    percent: d.percent ?? "",
    count: "",
    filter: "all",
    seed: d.seed ?? "",
    split: "same",
    days: d.expires_in_days ?? policy?.expiry_days?.[0] ?? "",
  };
}

/** The POST body of the form (`{emails, sample, expires_in_days}`). */
export function inviteBody(v, { metric } = {}) {
  return {
    emails: v.emails,
    sample: {
      mode: v.mode,
      value:
        v.mode === "percent"
          ? Number(v.percent)
          : v.mode === "count"
            ? Number(v.count)
            : null,
      seed: Number(v.seed),
      filter: { key: v.filter, metric: metric ?? null },
      split: v.split,
    },
    expires_in_days: Number(v.days),
  };
}

/** What blocks sending, or null. */
export function inviteProblem(v, pool) {
  if (!v.emails.length) return "Add at least one email";
  const bad = v.emails.find((e) => !isEmail(e));
  if (bad) return `${bad} is not an email address`;
  if (!pool) return "The sample has no items";
  if (v.mode === "percent" && !(Number(v.percent) > 0))
    return "Enter a percentage";
  if (v.mode === "count" && !(Number(v.count) > 0)) return "Enter a count";
  if (!Number.isFinite(Number(v.seed)) || v.seed === "") return "Enter a seed";
  const k = sampleSize(pool, formSample(v));
  if (v.split === "split" && k < v.emails.length)
    return `${k} items can't be shared between ${v.emails.length} reviewers`;
  return null;
}

/** Start review: what blocks the outside invites, or null. */
export const outsideProblem = (value, total) =>
  value?.on ? inviteProblem(value, total) : null;
