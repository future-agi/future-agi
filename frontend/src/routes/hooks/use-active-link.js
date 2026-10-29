import { useLocation } from "react-router-dom";

// ----------------------------------------------------------------------

/*
  Simulate-v2 nav highlight. A run and everything under it — the Runs
  step, a run (/runs/:runId), compare — sits under its environment, so
  Environments stays lit. (It used to switch to a "Simulated Runs" entry,
  which the sidebar no longer has, so a run lit nothing.)

  An improvement page lives at /simulate/improvements/:envId/:optId, but
  opened from a run (its Improvements tab) the person is still inside that
  environment: the page carries the run it came from in `state.back`, and
  Environments stays lit instead of Improvements.
*/
const ENVIRONMENTS_PATH = "/dashboard/simulate/environments";
const IMPROVEMENTS_PATH = "/dashboard/simulate/improvements";
const IMPROVEMENT_PAGE_RE = /^\/dashboard\/simulate\/improvements\/[^/]+\/[^/]+/;

export function useActiveLink(path, _deep = true) {
  const { pathname, state } = useLocation();
  const fromEnvironment =
    IMPROVEMENT_PAGE_RE.test(pathname) &&
    String(state?.back?.to || "").startsWith(ENVIRONMENTS_PATH);

  if (fromEnvironment) {
    if (path === IMPROVEMENTS_PATH) return false;
    if (path === ENVIRONMENTS_PATH) return true;
  }
  return pathname.startsWith(path);
}
