/*
  Keywords — where a scenario came from, kept on the scenario so production
  cases stay findable after they join the suite (Kartik's review, C7: "failed
  in production, passes now" has to be something you can filter to).

  A scenario added from outside the environment carries its source keywords
  automatically, more than one when more than one is true:
    from-production   Observe sessions, traces or calls; a production replay
    from-issue        an Error Feed issue, or an epoch run for one (an issue
                      is built from production traces, so it is from
                      production too)
    from-dataset      an uploaded dataset
    from-transcript   uploaded transcripts
  Generated, hand-written, chat-written and derived scenarios carry none — a
  keyword is never invented for them. Keywords someone already set (the
  import's own keyword field, a `keywords` list) are kept.

  Tags stay off as a way to group scenarios (scenarioOrgFlags.js): keywords
  are filtered on and read on the row's source chip, and suites remain the
  one grouping.
*/
import { ensureProvenance } from "./scenarioProvenance";

export const FROM_PRODUCTION = "from-production";
export const FROM_ISSUE = "from-issue";
export const FROM_DATASET = "from-dataset";
export const FROM_TRANSCRIPT = "from-transcript";

/* The order keywords read in: the most specific source first. */
const ORDER = [FROM_ISSUE, FROM_PRODUCTION, FROM_DATASET, FROM_TRANSCRIPT];

/* By the scenario's source (scenarioProvenance SOURCES ids). */
const BY_SOURCE = {
  production: [FROM_PRODUCTION],
  "error-feed": [FROM_ISSUE, FROM_PRODUCTION],
  "dataset-import": [FROM_DATASET],
  transcript: [FROM_TRANSCRIPT],
};
/* By the kind of evidence an import drafted it from (traceImport.sourceKind).
   Simulated calls and failed simulations are this product's own runs, not
   outside evidence — no keyword. */
const BY_IMPORT_KIND = {
  observe: [FROM_PRODUCTION],
  errorFeed: [FROM_ISSUE, FROM_PRODUCTION],
  dataset: [FROM_DATASET],
  transcript: [FROM_TRANSCRIPT],
};

const uniq = (list) => [...new Set(list.filter(Boolean))];
const ordered = (list) =>
  uniq(list).sort((a, b) => {
    const ia = ORDER.indexOf(a);
    const ib = ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return 0;
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });

/** The source keywords a scenario's provenance gives it (never for generated or hand-written). */
export const sourceKeywordsOf = (row) => {
  if (!row) return [];
  const out = [];
  if (row.errorFeedIssue?.id) out.push(FROM_ISSUE, FROM_PRODUCTION);
  if (row.traceImport)
    out.push(...(BY_IMPORT_KIND[row.traceImport.sourceKind || "observe"] || []));
  /* The source the row's chip already names — so a chip reading "Dataset"
     and the from-dataset keyword never disagree. */
  const source = row.source || row.proposed?.source || ensureProvenance(row).source;
  out.push(...(BY_SOURCE[source] || []));
  return ordered(out);
};

/** Every keyword a scenario carries: the ones it was given, then its source's. */
export const keywordsOf = (row, envState) => {
  if (!row) return [];
  const tagNames = (envState?.scenarioTags || [])
    .filter((t) => (row.tagIds || []).includes(t.id))
    .map((t) => t.name);
  return ordered([...(row.keywords || []), ...sourceKeywordsOf(row), ...tagNames]);
};

/** The row stamped with its source keywords, keeping any it already had. */
export const withSourceKeywords = (row, extra = []) => {
  const keywords = ordered([...(row?.keywords || []), ...extra, ...sourceKeywordsOf(row)]);
  return keywords.length ? { ...row, keywords } : row;
};

/** The keywords in use across these scenarios, each with how many carry it. */
export const keywordChoicesOf = (rows, envState) => {
  const counts = new Map();
  (rows || []).forEach((r) =>
    keywordsOf(r, envState).forEach((k) => counts.set(k, (counts.get(k) || 0) + 1)),
  );
  return ordered([...counts.keys()]).map((k) => ({ id: k, label: `${k} (${counts.get(k)})`, count: counts.get(k) }));
};

/** Does this scenario pass a Keywords filter — any of `is`, none of `isNot`. */
export const matchesKeywords = (row, envState, { is = [], isNot = [] } = {}) => {
  if (!is.length && !isNot.length) return true;
  const mine = keywordsOf(row, envState);
  if (is.length && !is.some((k) => mine.includes(k))) return false;
  if (isNot.length && isNot.some((k) => mine.includes(k))) return false;
  return true;
};
