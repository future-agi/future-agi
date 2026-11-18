/*
  Files added while a new environment's design waits for approval (plan:
  "Attach a policy and optional CSV" in the builder, before the build). They
  aren't in any version yet; approving builds the first version with what
  they propose — rules and seed records — so there's no second version
  straight after.
*/
import {
  acceptDataPatch,
  dataProposalOf,
  readFile,
  sourceFromOrg,
} from "./orgData";
import { castOf } from "./actors";

/** Sources added to the design, not yet part of a version. */
export const designSourcesOf = (envState) =>
  (envState?.sources || []).filter(
    (s) => !s.archived && !s.inEnv && s.status !== "unsupported",
  );

const proposalFor = (env, envState, sources) =>
  dataProposalOf(sources, {
    cast: castOf(env, envState),
    tools: (env?.tools || []).map((t) => t.name),
    scenarios: envState?.scenarios || [],
  });

const takenOf = (proposal) =>
  proposal.items.filter(
    (i) => !i.blocking && !["hidden", "unreadable"].includes(i.kind),
  );

/* Files the builder couldn't use, each shown on its own (builder.md §1:
   "An unreadable file is shown individually"). */
const unusableOf = (envState) =>
  (envState?.sources || []).filter(
    (s) =>
      !s.archived &&
      !s.inEnv &&
      ["unsupported", "unreadable"].includes(s.status),
  );

/**
 * What the design's files add: { sources, rules, records, needsInput,
 * tableItems, ruleItems, unusable } — the counts for the summary line and
 * the items themselves, each citing its file, for the design's lists.
 */
export const designSourcesSummary = (env, envState) => {
  const sources = designSourcesOf(envState);
  const unusable = unusableOf(envState);
  if (!sources.length)
    return {
      sources,
      rules: 0,
      records: 0,
      needsInput: 0,
      tableItems: [],
      ruleItems: [],
      unusable,
    };
  const proposal = proposalFor(env, envState, sources);
  const taken = takenOf(proposal);
  const tableItems = taken
    .filter((i) => i.kind === "table")
    .map((i) => ({ name: i.table.name, rows: i.table.rows, src: i.cite.ref }));
  const ruleItems = taken
    .filter((i) => i.kind === "rule")
    .map((i) => ({ id: i.id, text: i.rule, src: i.cite.ref }));
  return {
    sources,
    rules: ruleItems.length,
    records: tableItems.reduce((n, t) => n + (t.rows || 0), 0),
    needsInput: proposal.items.filter((i) => i.blocking).length,
    tableItems,
    ruleItems,
    unusable,
  };
};

/**
 * Nothing to build: no records and no actions, from the description or its
 * files. Rules alone aren't a world — the agent needs something to act on.
 */
/* Nothing to build until it knows what the agent does: no tools, no
   actions to test — whatever records or rules the files hold. */
export const designIsEmpty = (env) => !(env?.tools || []).length;

/** The envState patch approving the design applies, or null with no files. */
export const designSourcesPatch = (env, envState) => {
  const sources = designSourcesOf(envState);
  if (!sources.length) return null;
  const proposal = proposalFor(env, envState, sources);
  const { ruleIds: _ruleIds, ...rest } = acceptDataPatch(
    env,
    envState,
    proposal,
    takenOf(proposal).map((i) => i.id),
    {
      cast: castOf(env, envState),
      inVersion: envState?.activeEnvVersion || "v1",
    },
  );
  /* Scenarios come from the build itself; leave its list alone. */
  delete rest.scenarios;
  return rest;
};

/** The patch adding files (File objects or { name, size }) to the design. */
export const addDesignFilesPatch = (envState, files) => ({
  sources: [...(envState?.sources || []), ...files.map(readFile)],
});

/** The patch adding a Knowledge base document, or null without access. */
export const addDesignOrgPatch = (envState, o) =>
  o.access
    ? { sources: [...(envState?.sources || []), sourceFromOrg(o)] }
    : null;

/* Text files are read for real (their words, not their name). */
const READABLE = /\.(md|markdown|txt)$/i;

/** Read the text of what can be read in the browser; the rest as is. */
export const readDesignFiles = (files) =>
  Promise.all(
    Array.from(files || []).map(async (f) =>
      READABLE.test(f?.name || "") &&
      typeof f?.text === "function" &&
      (f.size || 0) < 512 * 1024
        ? {
            name: f.name,
            size: f.size,
            type: f.type,
            text: await f.text().catch(() => null),
          }
        : f,
    ),
  );

const sameCopy = (a, b) =>
  a.name === b.name &&
  a.sizeKb === b.sizeKb &&
  JSON.stringify(a.parsed?.passages || a.parsed?.table || null) ===
    JSON.stringify(b.parsed?.passages || b.parsed?.table || null);

/**
 * Add files to a design (builder.md §1, data.md "Choose a source"). Sources
 * add up: a different file joins the others. The same file name with new
 * contents replaces the earlier copy in place — it's that file's newer
 * revision, and nothing is saved yet. An identical copy is skipped.
 * Returns { sources, added, replaced, skipped, fresh } — `fresh` are the
 * read sources that are new or changed.
 */
export const mergeDesignFiles = (envState, files) => {
  let sources = [...(envState?.sources || [])];
  const out = { added: [], replaced: [], skipped: [], fresh: [] };
  files.forEach((f) => {
    const read = readFile(f);
    const at = sources.findIndex(
      (s) => !s.archived && !s.inEnv && s.name === read.name,
    );
    if (at < 0) {
      sources = [...sources, read];
      out.added.push(read.name);
      out.fresh.push(read);
    } else if (sameCopy(sources[at], read)) {
      out.skipped.push(read.name);
    } else {
      const prev = sources[at];
      const next = {
        ...read,
        id: prev.id,
        version: (prev.version || 1) + 1,
        versions: [
          ...(prev.versions || []),
          {
            v: (prev.version || 1) + 1,
            at: new Date().toISOString(),
            note: "Replaced with a newer copy",
          },
        ],
      };
      sources = sources.map((s, i) => (i === at ? next : s));
      out.replaced.push(read.name);
      out.fresh.push(next);
    }
  });
  return { sources, ...out };
};
