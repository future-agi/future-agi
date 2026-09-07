import { useLocation } from "react-router-dom";

// ----------------------------------------------------------------------

/*
  Simulate-v2 nav-highlight override:
    /simulate/environments/:envId/runs/:runId  →  a run view, not an
    environment view. The URL nests under `/environments` for state
    reasons, but the *user's* current context is a simulation run — so
    the sidebar should highlight "Simulated Runs" instead of
    "Environments" while a run is open. Two-way switch:
      - When on a run URL, `Environments` de-activates.
      - When on a run URL, `Simulated Runs` (path `/simulate/test`)
        activates even though the pathname doesn't start with it.
*/
/* Matches every run-related view under /environments/:envId — a
   specific run (/runs/:runId), the "Runs" step on the workspace
   (/runs), or the compare-runs screen (/compare). All three are
   simulation contexts, so the sidebar should read "Simulated Runs". */
const RUN_URL_RE = /^\/dashboard\/simulate\/environments\/[^/]+\/(?:runs(?:\/[^/]+)?|compare)(?:\/|$)/;
const SIMULATED_RUNS_PATH = "/dashboard/simulate/test";
const ENVIRONMENTS_PATH = "/dashboard/simulate/environments";

export function useActiveLink(path, _deep = true) {
  const { pathname } = useLocation();
  const onRun = RUN_URL_RE.test(pathname);

  if (onRun) {
    if (path === ENVIRONMENTS_PATH) return false;
    if (path === SIMULATED_RUNS_PATH) return true;
  }
  return pathname.startsWith(path);
}
