// The baseline / candidate pair a page is about (iterate r1 G3 P4), as the
// page header picks it: on the experiment page the baseline is `baseline=`,
// else the pinned run, else the first run; the candidate is `cand=`, else
// the agent's latest version, else the analysis' best verdict, else the
// first other run (pageCandidate). Paths opens on this pair; the fix-run
// banner reads it to know what Paths shows.
import { useRunConfigs } from "src/api/develop/r2";
import { useDatasetRuns } from "src/api/develop/runs";
import { useCandidateRunId } from "src/api/develop/analysis";
import { experimentDefaultBaseline } from "../results/useResultsUrlState";
import { pageCandidate } from "../results/pageCandidate";

/**
 * @param {{datasetId?: string, experimentId?: string, runIds?: string[],
 *   baselineId?: string|null, search: string}} args  `baselineId` = the
 *   page's baseline on the compare page (ignored with `experimentId`)
 * @returns {{baselineId: string|null, candidateId: string|null}}
 */
export default function usePagePair({
  datasetId,
  experimentId,
  runIds = [],
  baselineId: baselineProp,
  search,
}) {
  const sp = new URLSearchParams(search ?? "");
  const { data: cfg } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const { data: datasetRuns } = useDatasetRuns(datasetId, {
    enabled: Boolean(datasetId && experimentId),
  });
  const requested = sp.get("baseline");
  const baselineId = experimentId
    ? experimentDefaultBaseline(runIds, requested, datasetRuns?.pinned_run_id)
    : baselineProp ?? null;
  const cand = sp.get("cand");
  const best = useCandidateRunId(datasetId, {
    runIds,
    baselineId,
    // The same analysis read (cache entry) as the page header.
    analysisBaselineId: experimentId
      ? requested === "none" || runIds.includes(requested)
        ? requested
        : undefined
      : baselineId ?? undefined,
    cand,
  });
  const candidateId = pageCandidate({
    runs: cfg?.results ?? [],
    runIds,
    baselineId,
    cand,
    fallback: best,
    // IT-R4 G3 (P2): Fix this step on the baseline (`compared=`) keeps it.
    holdSubject: Boolean(sp.get("compared")),
  }).id;
  return { baselineId, candidateId };
}
