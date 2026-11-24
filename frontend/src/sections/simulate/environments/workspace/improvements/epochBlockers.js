// What to do about each reason Run Epoch can't start, by the server's code. The
// server's message says why; this names the next step and where it is. Pure, so
// the issue drawer and the run's launch form say the same thing.
import { paths } from "src/routes/paths";

const environments = paths.dashboard.simulate.environments;

const openEnvironment = (envId) => ({
  label: "Open the environment",
  href: environments.detail(envId),
});

// A page that fixes it.
const ACTIONS = {
  environment_not_built: openEnvironment,
  environment_build_failed: openEnvironment,
  environment_update_failed: openEnvironment,
  environment_updating: openEnvironment,
  fix_draft_open: (envId) => ({
    label: "Review unsaved changes",
    href: environments.workspaceTab(envId, "scenarios"),
  }),
  issue_scenarios_limit: (envId) => ({
    label: "Open scenarios",
    href: environments.workspaceTab(envId, "scenarios"),
  }),
  fix_run_active: (envId) => ({
    label: "Open its epochs",
    href: environments.workspaceTab(envId, "improvements"),
  }),
  usage_limit: () => ({
    label: "See usage",
    href: paths.dashboard.settings.usageSummary,
  }),
  fix_github_not_connected: () => ({
    label: "Connect GitHub",
    href: paths.dashboard.settings.integrations,
  }),
};

// No page fixes these: one sentence on what to do, under the server's reason.
const HINTS = {
  fix_unavailable: "Ask your Future AGI admin to turn on agent versions.",
  // With versions on, only an environment made before they were kept has none.
  drafts_unavailable:
    "It was made before versions were kept: ask your Future AGI admin to save its versions.",
  fix_agent_not_editable:
    "Pick an environment whose agent is uploaded or from GitHub.",
  fix_targets_too_few:
    "Keep more of the drafted scenarios, or Run Epoch again and pick other traces.",
};

// The link that fixes the blocker, or null.
export const blockerAction = (code, envId) => ACTIONS[code]?.(envId) ?? null;

// What to do when no page fixes it, or null.
export const blockerHint = (code) => HINTS[code] ?? null;

// How the winner ships, in words, before launch. A pull request that can't open
// now ships as a patch; the server's reason says what a pull request needs.
const SHIP_PATH = {
  pull_request: "A pull request on the agent's repository",
  make_default: "A new version of the agent, made the default when you choose",
};

export const shipHeadline = (ship) =>
  ship.path === "pull_request" && ship.blocked
    ? "A patch to download"
    : SHIP_PATH[ship.path];

// The cases held back to prove the fix, as the launch form shows them: the keys
// the server picked for the suggested targets, or why there are none to show.
// Below `cases_min` the epoch writes variations first and picks them with those;
// `edited`: the targets differ from the suggested ones, so the launch picks them.
export function heldBackOf(options, targets) {
  const keys = options.held_back_scenario_keys;
  const suggested = options.suggested.target_scenario_keys;
  const same =
    targets.length === suggested.length &&
    suggested.every((key) => targets.includes(key));
  if (same && keys != null) return { keys };
  if (targets.length < (options.limits?.cases_min ?? 0)) {
    return { pending: "variations" };
  }
  return same ? { keys: [] } : { pending: "edited" };
}

export const HELD_BACK_PENDING = {
  variations: "The cases held back to prove the fix are chosen after the variations are written.",
  edited:
    "You changed the cases to fix, so the ones held back to prove the fix are chosen from them when the epoch starts.",
};
