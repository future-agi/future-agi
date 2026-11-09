// The parity program's hide switches (PARITY-PROGRAM.md section 4, lane P22).
//
// Each one is the "hide if missed" state of a gap, built now and shipped in
// that state until the lane that closes the gap is accepted and the release
// turns the switch on. Off stays hidden or uses honest wording (2026-10-19),
// except RL 1808's hosted Sandboxes/Deployments sidebar information below:
// visible title + deployment-unavailable label, entirely inert, never a page or preview.
//
// A switch is a part the server reports (OMEGA_PART in rollout.js, decided in
// omega_rollout/readiness.py from the release's OMEGA_RELEASED_PARTS). Read it
// with useOmegaPart(part): it is false while the answer loads, on an error and
// on an older server without the entry, so a page always starts in the off
// state. The helpers below word each switch's two states in one place, so every
// surface says the same thing and a test checks both.
//
// The program's other switches are decided elsewhere, each from the fact it
// depends on:
// - Sandboxes and Deployments on hosted Future AGI: the server withholds the
//   products themselves (omega_rollout/service.py, parts hosted_sandboxes and
//   hosted_deployments). RL 1808 keeps their sidebar titles with inert
//   deployment-unavailable labels once capabilities confirms hosted + withheld. Their
//   route gates still render the standard not-found; API refusal is unchanged.
//   The product switcher lists Future AGI, AI Gateway and Omega Console, not
//   these two sidebar products. Self-hosted/preview and master OFF are unchanged.
// - "Live" or "Running on this host": per deployment, from who EE says can
//   reach its URL (deployments/lib/reach.js, EE's urlReach). P09 turns it to
//   Live by giving a deployment a public endpoint; no release flag could say
//   it more truly.
// - The Studio tab's install step: shown once the release names the package
//   that runs omega dev (studio/release-config.js, VITE_OMEGA_CLI_PACKAGE),
//   which P03 sets when it is published.
// - The agent's capability text, the Verified mark and Usage spend live in the
//   Omega Console, which EE serves (its release-switches and
//   capability-statement modules).

import { OMEGA_PART } from "./rollout";

// Exact hosted takeover wording from 7f132adf; one label for built products
// withheld by this deployment, never a promise about future availability.
export const DEPLOYMENT_UNAVAILABLE = "Not available in this deployment";

/** The switches this module words: who turns each on, and where it shows. */
export const P22_SWITCHES = Object.freeze([
  {
    part: OMEGA_PART.DEPLOY_NO_DOWNTIME,
    flippedBy: "P19 PX-DEPLOY-BLUEGREEN: no failed request across a switch",
    shows: "Roll back dialog, settings redeploy note, new revision note",
  },
  {
    part: OMEGA_PART.DEPLOY_LANGGRAPH_JS,
    flippedBy:
      "P23 PX-LANGGRAPH-JS: an unmodified LangGraph.js project deploys",
    shows: "New deployment description, Deployments subtitle and empty state",
  },
  {
    part: OMEGA_PART.SDK_PYTHON,
    flippedBy: "SDK publish: the Python package is published on PyPI",
    shows: "Code panels on Sandboxes and Deployments pages",
  },
  {
    part: OMEGA_PART.SDK_TYPESCRIPT,
    flippedBy: "SDK publish: the TypeScript package is published on npm",
    shows: "Code panels on Sandboxes and Deployments pages",
  },
]);

/**
 * What a switch to another revision does to callers (deploy, redeploy,
 * rollback). Today the serving container stops before the next one starts:
 * measured at 3.2 s for a redeploy and 3.4 s for a rollback. Once a switch
 * keeps the serving revision up until the next one is healthy
 * (DEPLOY_NO_DOWNTIME), the sentence says that instead.
 */
export function switchoverSentence(noDowntime, revision) {
  const next =
    revision === undefined || revision === null
      ? "the new revision"
      : `revision ${revision}`;
  if (noDowntime === true) {
    return `The revision serving now keeps serving until ${next} answers its health check, and stays live if it never does.`;
  }
  return `The agent is without service for about 3 seconds while ${next} starts and answers its health check.`;
}

/**
 * What Deployments can deploy. Omega projects in JavaScript or TypeScript
 * only, until a LangGraph.js project with langgraph.json deploys unchanged
 * (DEPLOY_LANGGRAPH_JS).
 */
export function projectWords(langgraphJs) {
  if (langgraphJs === true) {
    return {
      kind: "Omega and LangGraph.js projects (JS/TS)",
      one: "an Omega or LangGraph.js project (JS/TS)",
      folder: "a folder with omega.studio.json or langgraph.json",
    };
  }
  return {
    kind: "Omega projects (JS/TS)",
    one: "an Omega project (JS/TS)",
    folder: "a folder with omega.studio.json",
  };
}

/** The tabs a code panel offers. curl always; an SDK only once it is published. */
export const CODE_TAB = Object.freeze({
  CURL: "curl",
  PYTHON: "python",
  TYPESCRIPT: "typescript",
});

export function codeTabs({ python = false, typescript = false } = {}) {
  return [
    CODE_TAB.CURL,
    ...(python === true ? [CODE_TAB.PYTHON] : []),
    ...(typescript === true ? [CODE_TAB.TYPESCRIPT] : []),
  ];
}
