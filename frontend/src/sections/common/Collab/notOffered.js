// Parts the launch registry lists that a deployment does not offer yet: each
// one stays on screen, turned off, and says the shared line every turned-off
// part says (NOT_IN_RELEASE). The answer is the registry's (collabOffer.js
// surfaceOffered), so the show-unreleased switch does not change it, and
// nothing reaches a route the deployment does not serve. Prototype builds
// and the unit-test profile offer every part.
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { surfaceOffered } from "./collabOffer";

const PARTS = Object.freeze([
  "list.organize.metadata",
  "list.organize.agents",
  "dataset.collab.translate",
  "dataset.rowExtras.runs",
  "evals.remote",
]);

/**
 * The shared line where this deployment does not offer a listed part, else
 * null (offered, or a part not listed here).
 */
export const notOfferedLine = (surface) =>
  PARTS.includes(surface) && !surfaceOffered(surface) ? NOT_IN_RELEASE : null;
