import { runOnRowsHref } from "src/sections/common/RunSources/runOnRows";

/**
 * v2.1 g8 (entry:run-on-rows:training-split): Training set › New experiment
 * on the test split runs on the export's version, not on latest. The store
 * prefill carries the version for this open; the deep link names it too
 * (`version_id`, owed g2 runOnRows + g3 RunExperiment patches) so a reload
 * reopens on the same version. Pure.
 * @returns {{href: string, prefill: {split_name: string, version_id?: string}}}
 */
export function trainingSetDoor({ datasetId, testName, version } = {}) {
  return {
    prefill: {
      ...(version?.id ? { version_id: version.id } : {}),
      split_name: testName,
    },
    href: runOnRowsHref({
      datasetId,
      split: testName,
      ...(version?.id ? { datasetVersionId: version.id } : {}),
    }),
  };
}
