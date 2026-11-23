/*
  What a scenario cites — the rules and files it rests on — and whether each
  citation still holds.

  Kept apart from its Source (where the scenario came from: by hand, derived,
  a trace). A scenario written by hand can still cite a rule; one derived
  from a file cites that file.

  Each citation is one of:
    confirmed     the rule or passage reads as it did when the case was proved
    changed       it moved on since — a rule re-worded, a file with a newer
                  version or revision waiting. This is what "Source changed"
                  means on the scenario.
    unconfirmed   it can't be checked: the rule was removed, the file is gone
                  from the sources, or the reference was typed in by hand

  Read from what rows already carry: the rule the expectation quotes, the
  sub-goals that test a rule, a spec change that made it Outdated, the file
  it was derived from (`citedSource` + `citation`), and the evidence attached
  to it (`grounding`).
*/
import { rulesOf, ruleOfScenario } from "./envModel";
import { envLabelOf } from "./envVersionKinds";
import { placeOf } from "./orgData";
import { kbChangesOf, kbRefreshOf } from "./kbCatalog";
import { sourceChangeOf } from "./sourceChange";

export const CITE_STATUS_LABEL = {
  confirmed: "Confirmed",
  changed: "Changed since",
  unconfirmed: "Couldn't be confirmed",
};

const QUOTED_RULE = /rule: "(.+?)"/;

const RULE_HOME = {
  prompt: "the agent's prompt",
  description: "the environment description",
  added: "added here",
};

const ruleHome = (r) =>
  r.source === "file"
    ? r.sourceName || "a file"
    : RULE_HOME[r.source] || "the agent's prompt";

/* "Returns policy.pdf · p.3" → ["Returns policy.pdf", "p.3"]. */
const splitRef = (ref) => {
  const [name, ...rest] = String(ref || "").split(" · ");
  return [name || null, rest.join(" · ") || null];
};

const pageOfRef = (ref) => {
  const m = String(ref || "").match(/\b(?:p\.|section )(\d+)/i);
  return m ? Number(m[1]) : null;
};

const liveSource = (sources, id) => {
  const src = id ? sources.find((s) => s.id === id) : null;
  return src && !src.archived && !src.revoked ? src : null;
};

const GONE = "The file is no longer in this environment's sources.";

/* ── rules ───────────────────────────────────────────────────────────────── */

const ruleCites = (row, { env, envState, goals }) => {
  const rules = rulesOf(env, envState);
  const quoted =
    (row?.expected || row?.outcome || "").match(QUOTED_RULE)?.[1] || null;
  const main = ruleOfScenario(row, rules);
  const ids = [];
  const add = (id) => id && !ids.includes(id) && ids.push(id);
  add(main?.id);
  (goals || []).forEach((g) => add(/^SG-rule-(.+)$/.exec(g?.id || "")?.[1]));
  add(row?.outdatedBy?.ruleId);
  const checked = new Set(row?.ruleChecked || []);
  const vl = (l) => envLabelOf(env, envState, l);

  const out = ids.map((id) => {
    const r = rules.find((x) => x.id === id);
    if (!r)
      return {
        key: `rule-${id}`,
        kind: "Rule",
        name:
          id === row?.outdatedBy?.ruleId
            ? `Rule ${id}`
            : quoted || `Rule ${id}`,
        place: `${id} · no longer in the contract`,
        status: "unconfirmed",
        note: "The rule was removed, so there is nothing to check it against.",
      };
    const reworded = !!r.original && r.original !== r.text;
    const outdated = row?.outdatedBy?.ruleId === id;
    const unchecked =
      reworded && r.changedIn && !checked.has(`${id}@${r.changedIn}`);
    const changed = outdated || unchecked;
    return {
      key: `rule-${id}`,
      kind: "Rule",
      name: r.text,
      place: `${id} · ${ruleHome(r)}`,
      status: changed ? "changed" : "confirmed",
      note:
        changed && !reworded
          ? `Changed as a policy change in ${outdated && /^agent /.test(String(row.outdatedBy.version)) ? row.outdatedBy.version : vl(row.outdatedBy?.version) || "a later version"}.`
          : null,
      compare:
        changed && reworded
          ? {
              was: "When it was written",
              now: r.changedIn ? `Since ${vl(r.changedIn)}` : "Now",
              pairs: [{ key: id, place: null, from: r.original, to: r.text }],
            }
          : null,
    };
  });
  /* It quotes a rule the contract doesn't have. */
  if (quoted && !main)
    out.unshift({
      key: "rule-quoted",
      kind: "Rule",
      name: quoted,
      place: "Quoted in Passes when",
      status: "unconfirmed",
      note: "No rule in the contract reads like this one.",
    });
  return out;
};

/* ── files ───────────────────────────────────────────────────────────────── */

/* The file it was derived from. */
const derivedCite = (row, envState, sources) => {
  if (!row?.citedSource) return null;
  const [refName, refPlace] = splitRef(row.citation?.ref);
  const src = liveSource(sources, row.citedSource);
  const base = {
    key: `file-${row.citedSource}`,
    kind: "File",
    name: src?.name || refName || "A file",
    excerpt: row.citation?.excerpt || null,
  };
  const place = (v) =>
    [refPlace, v ? `v${v}` : null].filter(Boolean).join(" · ") || null;
  if (!src)
    return { ...base, place: place(), status: "unconfirmed", note: GONE };
  const change = sourceChangeOf(row, envState);
  if (change) {
    const { pending } = change;
    return {
      ...base,
      place: place(src.version),
      status: "changed",
      note: `v${pending.v} is waiting. Runs still use v${src.version}.`,
      compare: {
        was: `v${src.version}`,
        now: `v${pending.v}`,
        pairs: (pending.changed || []).map((c) => ({
          key: c.id,
          place: placeOf(src, c.page),
          from: c.from,
          to: c.to,
        })),
      },
      fix: { type: "source", source: src, pending },
    };
  }
  const kb = kbRefreshOf(src);
  if (kb) {
    return {
      ...base,
      place: place(src.version),
      status: "changed",
      note: `Revision ${kb.latest} is in the Knowledge base. Runs keep using your copy until you review it.`,
      compare: {
        was: `Revision ${kb.from}`,
        now: `Revision ${kb.latest}`,
        pairs: kbChangesOf(src.kb.fileId, kb.latest)
          .filter((c) => c.from || c.to)
          .map((c) => ({
            key: c.id,
            place: c.ref || null,
            from: c.from || null,
            to: c.to || null,
          })),
      },
      fix: { type: "kb", source: src },
    };
  }
  const kept =
    row.sourcePinned?.sourceId === src.id && src.pendingRefresh
      ? `Kept on v${row.sourcePinned.v}. v${src.pendingRefresh.v} is waiting in Sources.`
      : null;
  return {
    ...base,
    place: place(row.sourcePinned?.v || src.version),
    status: "confirmed",
    note: kept,
  };
};

/* Evidence attached to it (ScenarioOrgPart's "Cite evidence"). */
const evidenceCites = (row, sources, skipRef) =>
  (row?.grounding || [])
    .filter(
      (g) => !(skipRef && g.sourceId === row.citedSource && g.ref === skipRef),
    )
    .map((g) => {
      const [refName, refPlace] = splitRef(g.ref);
      const src = liveSource(sources, g.sourceId);
      const base = {
        key: `ground-${g.id}`,
        kind: "File",
        name: src?.name || refName || "Cited evidence",
        place:
          [
            refPlace,
            g.version ? `v${g.version}` : null,
            g.pinned ? "pinned" : null,
          ]
            .filter(Boolean)
            .join(" · ") || null,
        excerpt: g.excerpt || null,
      };
      if (!g.sourceId)
        return {
          ...base,
          status: "unconfirmed",
          note: "Typed in by hand — there's no file to check it against.",
        };
      if (!src) return { ...base, status: "unconfirmed", note: GONE };
      if (g.version && src.version > g.version && !g.pinned) {
        const page = pageOfRef(g.ref);
        const now = page
          ? (src.parsed?.passages || []).find(
              (p) => p.page === page && p.text && p.text !== g.excerpt,
            )
          : null;
        return {
          ...base,
          status: "changed",
          note: `${src.name} is now v${src.version}. This still rests on v${g.version}.`,
          compare:
            g.excerpt && now
              ? {
                  was: `v${g.version}`,
                  now: `v${src.version}`,
                  pairs: [
                    {
                      key: g.id,
                      place: refPlace,
                      from: g.excerpt,
                      to: now.text,
                    },
                  ],
                }
              : null,
          fix: { type: "ground", id: g.id },
        };
      }
      return { ...base, status: "confirmed" };
    });

/**
 * Every citation on a scenario, rules first. `goals` = subGoalsOfScenario
 * (passed in when the caller has them). → [{ key, kind: "Rule" | "File",
 * name, place, status, note, excerpt, compare: { was, now, pairs }, fix }]
 */
export const citationsOf = (row, { env, envState, goals } = {}) => {
  if (!row) return [];
  const sources = envState?.sources || [];
  const derived = derivedCite(row, envState, sources);
  return [
    ...ruleCites(row, { env, envState, goals }),
    ...(derived ? [derived] : []),
    ...evidenceCites(row, sources, derived ? row.citation?.ref : null),
  ];
};

/** The first file citation that changed since — what "Source changed" names. */
export const changedFileCiteOf = (row, ctx) =>
  citationsOf(row, ctx).find(
    (c) => c.kind === "File" && c.status === "changed",
  ) || null;
