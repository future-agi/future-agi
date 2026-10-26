import { forcedCellsCoverage } from "../../_mock/coverage";
import { axesCoverage, pairsShare } from "./coverageAxes";

/*
  The three numbers the Coverage drawer leads with — Axes, Pairs, Forced —
  computed the same way CoverageMatrix does, so the toolbar button's hover
  and the drawer's header never disagree.
*/
export function coverageSummary(scenarios, env) {
  const perAxis = axesCoverage(scenarios, env);
  const forced = forcedCellsCoverage(scenarios);
  const axesHit = perAxis.filter((a) => a.levels.length > 0).length;
  const forcedHit = forced.filter((f) => f.present).length;
  return {
    axes: `${axesHit}/${perAxis.length}`,
    pairs: `${Math.round(pairsShare(scenarios, env) * 100)}%`,
    forced: `${forcedHit}/${forced.length}`,
  };
}
