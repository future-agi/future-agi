/*
  Review imported evidence — one review for every import that drafts
  scenarios from evidence (Observe sessions, traces or calls, an Error Feed
  issue, uploaded transcripts or datasets).

  - Review is compulsory: a draft joins the suite only when someone adds it,
    and waits until it is added or removed. Drafting was paid for, so nothing
    is thrown away without the user saying so.
  - A case the environment can't cover yet is never shown as a failed draft
    and never counted as one. The review names what the environment is
    missing, once, and hands that to the builder chat.
  - One builder chat changes any draft: "Edit with the builder" focuses the
    environment's chat on that draft.
*/
import { paths } from "src/routes/paths";
import { selectionNoun } from "./observeSelection";

const plural = (n, one, many = `${one}s`) => (n === 1 ? one : many);

/** A drafted scenario waiting for review (any import, or one import). */
export const waitingDraftsOf = (envState, importId = null) =>
  (envState?.scenarios || []).filter(
    (r) =>
      r.proposed &&
      r.traceImport &&
      !r.blocked &&
      (!importId || r.traceImport.id === importId),
  );

/** Cases the environment can't cover yet — kept out of sight, never listed. */
export const uncoveredCasesOf = (envState, importId = null) =>
  (envState?.scenarios || []).filter(
    (r) =>
      r.proposed &&
      r.traceImport &&
      r.blocked &&
      (!importId || r.traceImport.id === importId),
  );

/* Checks that are about the environment (its records, tools and rules) —
   a case failing one waits for the environment, not for the reviewer. The
   others are about the draft itself, which the review settles. */
const ENV_GATES = new Set(["data", "tools", "rules", "staged"]);
export const isEnvironmentGap = (blocked) =>
  !!blocked && ENV_GATES.has(blocked.gate);

/** What the environment would have to cover, in the user's words. */
export const coverageGapOf = (blocked) => {
  if (blocked?.needs) return blocked.needs;
  const reason = String(blocked?.reason || "");
  const names = reason.match(/It names (.+?), which isn't/);
  if (names) {
    const ids = names[1].split(/,\s*/);
    return `${plural(ids.length, "the record", "the records")} ${ids.join(", ")}`;
  }
  const needs = reason.match(/It needs (.+?), which this environment/);
  if (needs) {
    const tools = needs[1].split(/,\s*/);
    return `${plural(tools.length, "the tool", "the tools")} ${tools.join(", ")}`;
  }
  const days = reason.match(/It expects (\d+) days/);
  if (days) return `a ${days[1]}-day window in its rules`;
  if (blocked?.gate === "staged" || /presumes/.test(reason))
    return "the records these calls start from";
  return "what these calls rely on";
};

/* Where the evidence came from, as the line and the nudge say it. */
const ORIGIN = {
  observe: "production",
  errorFeed: "production",
  transcript: "uploaded transcripts",
  dataset: "an uploaded dataset",
  simCall: "simulated calls",
  simulation: "failed simulations",
};
const kindOf = (r) => r?.traceImport?.sourceKind || "observe";
export const originOf = (rows) => {
  const kinds = [
    ...new Set(rows.map((r) => ORIGIN[kindOf(r)] || "production")),
  ];
  return kinds.length === 1 ? kinds[0] : "imported evidence";
};
const caseWord = (rows) =>
  originOf(rows) === "production" ? "production case" : "case";

/**
 * The one environment-level nudge: how many cases need the environment to
 * cover something, and what. `null` when every case could be drafted.
 */
export const coverageNudgeOf = (uncovered) => {
  if (!uncovered.length) return null;
  const whats = [...new Set(uncovered.map((r) => coverageGapOf(r.blocked)))];
  const what =
    whats.length <= 2
      ? whats.join(" and ")
      : `${whats.slice(0, 2).join(", ")} and ${whats.length - 2} more`;
  const n = uncovered.length;
  const noun = plural(n, caseWord(uncovered), `${caseWord(uncovered)}s`);
  return {
    n,
    what,
    ids: uncovered.map((r) => r.id),
    line: `${n} ${noun} ${n === 1 ? "needs" : "need"} the environment to cover ${what}`,
    /* Handed to the builder chat as is — the chat says it back. */
    scaffold: {
      label: `Cover ${what}`,
      prompt: `Update the environment so it covers ${what} — ${n} ${noun} from imported evidence ${n === 1 ? "needs" : "need"} it`,
      icon: "solar:globus-linear",
      placeholder: "Add anything the builder should know, or just send",
    },
  };
};

/** The one quiet line on the Scenarios tab, or null when nothing waits. */
export const waitingLineOf = (envState) => {
  const drafts = waitingDraftsOf(envState);
  if (drafts.length) {
    const n = drafts.length;
    return {
      kind: "review",
      text: `${n} drafted ${plural(n, "scenario")} from ${originOf(drafts)} waiting for your review`,
      action: "Review",
    };
  }
  const nudge = coverageNudgeOf(uncoveredCasesOf(envState));
  return nudge
    ? { kind: "cover", text: nudge.line, action: "Review", nudge }
    : null;
};

/** The source keyword an imported scenario carries everywhere it's listed. */
export const importKeywordOf = (row) => {
  if (!row) return null;
  if (row.errorFeedIssue?.id) return "from-issue";
  if (row.traceImport && kindOf(row) === "errorFeed") return "from-issue";
  if (row.traceImport && kindOf(row) === "observe") return "from-production";
  if (!row.traceImport && row.source === "production") return "from-production";
  return null;
};

/** "Session 695cf943" / "Trace T-104" / "Transcript call-3.txt" — its id, as the source names it. */
export const caseIdLineOf = (row) => {
  const imp = row?.traceImport || {};
  const ids = imp.sourceIds || [];
  const kind = kindOf(row);
  if (kind === "transcript") return `Transcript ${ids.join(", ")}`;
  if (kind === "dataset") return `Dataset row ${ids.join(", ")}`;
  if (kind === "simulation") return `Simulated call ${ids.join(", ")}`;
  const unit = imp.unit || (kind === "simCall" ? "calls" : "traces");
  const noun = selectionNoun(unit, ids.length);
  return `${noun.charAt(0).toUpperCase()}${noun.slice(1)} ${ids.join(", ")}`;
};

/** Where its source opens in Observe — production and simulated calls only. */
export const observeHrefOf = (row) => {
  const kind = kindOf(row);
  if (!["observe", "errorFeed", "simCall"].includes(kind)) return null;
  const id = row?.traceImport?.sourceIds?.[0];
  return `${paths.dashboard.observe}${id ? `?trace=${encodeURIComponent(id)}` : ""}`;
};

/** How a review's header names the import(s) it covers. */
export const importSummaryOf = (drafts, uncovered) => {
  const all = [...drafts, ...uncovered];
  const imports = [...new Set(all.map((r) => r.traceImport?.id))];
  if (!all.length) return "";
  if (imports.length > 1)
    return `From ${imports.length} imports · ${originOf(all)}`;
  const info = all[0].traceImport || {};
  const n = (info.ids || []).length;
  const unit =
    info.sourceKind === "errorFeed"
      ? "trace"
      : info.sourceKind === "transcript"
        ? "transcript"
        : info.sourceKind === "dataset"
          ? "dataset row"
          : selectionNoun(info.unit || "traces", 1);
  const from =
    info.sourceKind === "errorFeed"
      ? `Error Feed ${(info.issueIds || []).join(", ")}`
      : info.project
        ? info.project
        : null;
  const when = info.at ? importedWhen(info.at) : null;
  return [
    `${n} ${plural(n, unit)}${from ? ` from ${from}` : ""}`,
    when ? `imported ${when}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
};

const importedWhen = (iso) => {
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return null;
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} minutes ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h} ${plural(h, "hour")} ago`;
  const d = Math.round(h / 24);
  return `${d} ${plural(d, "day")} ago`;
};

/* ── the builder chat ─────────────────────────────────────────────────── */

const short = (s, n = 36) =>
  String(s || "").length > n
    ? `${String(s).slice(0, n - 1)}…`
    : String(s || "");

/** The chip "Edit with the builder" pins in the chat for one draft. */
export const draftEditScaffold = (row) => ({
  label: `Edit draft: ${short(row.title)}`,
  prompt: `Change the drafted scenario from ${caseIdLineOf(row)} (“${row.title}”)`,
  icon: "solar:pen-2-linear",
  placeholder:
    "Say what to change — e.g. cut the turns where the caller, not the agent, was at fault",
});

const EDIT =
  /^Change the drafted scenario from .+? \(“([\s\S]+?)”\)\.?\s*([\s\S]*)$/;
const COVER = /^Update the environment so it covers ([\s\S]+?) — \d+ /;

/**
 * The builder's answer to a message the review handed it — a draft to
 * change, or the environment to cover what some cases need. Returns
 * { patch, reply } or null when the message isn't one of these.
 */
export const importChatOf = (text, envState, { by } = {}) => {
  const rows = envState?.scenarios || [];
  const edit = String(text || "").match(EDIT);
  if (edit) {
    const [, title, ask] = edit;
    const row = waitingDraftsOf(envState).find((r) => r.title === title);
    if (!row)
      return {
        reply: `“${short(title, 60)}” isn't waiting for review any more — it was added or removed. Open it on the Scenarios tab to change it there.`,
      };
    if (!ask.trim())
      return {
        reply: `What should change in “${short(title, 60)}”? For example: cut the turns where the caller, not the agent, was at fault.`,
      };
    const change = ask.trim().replace(/\.$/, "");
    return {
      patch: {
        scenarios: rows.map((r) =>
          r.id === row.id
            ? {
                ...r,
                traceReview: {
                  ...(r.traceReview || {}),
                  builderEdits: [
                    ...(r.traceReview?.builderEdits || []),
                    { ask: change, at: new Date().toISOString(), by },
                  ],
                },
              }
            : r,
        ),
      },
      reply: `Changed the draft “${short(title, 60)}”: ${change.charAt(0).toLowerCase()}${change.slice(1)}. It's still waiting for your review on the Scenarios tab.`,
    };
  }
  const cover = String(text || "").match(COVER);
  if (cover) {
    const what = cover[1];
    const cases = uncoveredCasesOf(envState).filter((r) =>
      what.includes(coverageGapOf(r.blocked)),
    );
    if (!cases.length)
      return {
        reply: `The environment already covers ${what} — nothing is waiting on it.`,
      };
    const ids = new Set(cases.map((r) => r.id));
    const records = (r) =>
      r.blocked?.gate === "data"
        ? (String(r.blocked.reason).match(/It names (.+?), which/)?.[1] || "")
            .split(/,\s*/)
            .filter(Boolean)
        : [];
    return {
      patch: {
        scenarios: rows.map((r) =>
          ids.has(r.id)
            ? {
                ...r,
                blocked: null,
                syntheticRecords: [
                  ...new Set([...(r.syntheticRecords || []), ...records(r)]),
                ],
              }
            : r,
        ),
      },
      reply: `The environment now covers ${what}. ${cases.length} more drafted ${plural(cases.length, "scenario")} ${cases.length === 1 ? "is" : "are"} waiting for your review on the Scenarios tab.`,
    };
  }
  return null;
};

/** The user's removals, settled when they leave the review: the drafts go,
    and the import's history keeps that they were removed, by whom and when. */
export const removeDraftsPatch = (envState, rows, by) => {
  const ids = new Set(rows.map((r) => r.id));
  const at = new Date().toISOString();
  return {
    scenarios: (envState?.scenarios || []).filter((r) => !ids.has(r.id)),
    traceImports: [
      ...(envState?.traceImports || []),
      ...rows.map((r) => ({
        importId: r.traceImport?.id,
        draftId: r.id,
        title: r.title,
        sources: r.traceImport?.sourceIds,
        rejected: { reason: "Removed in review", by, at },
      })),
    ],
  };
};
