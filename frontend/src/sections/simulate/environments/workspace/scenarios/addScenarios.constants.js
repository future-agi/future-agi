import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";

// Copy + candidate derivation for the Add-scenarios drawer.
//
// The designer's drawer offered five routes (twin, production, dataset, script,
// generate). Four of those pull backends this phase has no fixture for — the
// dataset/script importers were stripped from the pool fixture, and the twin /
// production routes read twin backing and the Error Feed. So this phase ships the
// one route that stands on the ported pool: generate from the agent. The rest are
// deferred, not disabled-in-place — there is nothing to disable yet.

export const ADD_COPY = {
  title: "Add scenarios",
  subtitle:
    "Your agent's scenarios are already here. These add the ones deriving it could not know to write.",
  routeLabel: "Generated from your agent",
  routeBlurb:
    "Fresh probes derived from this environment's rules, tools and seed data — the ones not already on the list.",
  searchPlaceholder: "Search generated scenarios…",
  selectAll: "Select all",
  emptyTitle: "Nothing left to add",
  emptyBody: "Every scenario we can generate for this environment is already on the list.",
  cancel: "Cancel",
  confirm: (n) => (n === 1 ? "Add 1 scenario" : `Add ${n} scenarios`),
};

// The candidates are the generated pool minus whatever is already on the
// environment, matched on id so re-opening the drawer never re-offers a row that
// was just added.
export const addCandidates = (env, selected = []) => {
  const taken = new Set((selected || []).map((s) => s.id));
  return generatedPool(env).filter((r) => !taken.has(r.id));
};
