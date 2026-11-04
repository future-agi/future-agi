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

/** What the design's files add: { sources, rules, records, needsInput }. */
export const designSourcesSummary = (env, envState) => {
  const sources = designSourcesOf(envState);
  if (!sources.length) return { sources, rules: 0, records: 0, needsInput: 0 };
  const proposal = proposalFor(env, envState, sources);
  const taken = takenOf(proposal);
  return {
    sources,
    rules: taken.filter((i) => i.kind === "rule").length,
    records: taken
      .filter((i) => i.kind === "table")
      .reduce((n, i) => n + (i.table?.rows || 0), 0),
    needsInput: proposal.items.filter((i) => i.blocking).length,
  };
};

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
