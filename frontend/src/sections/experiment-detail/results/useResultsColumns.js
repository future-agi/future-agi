import { useEffect, useMemo } from "react";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import { legacyHiddenToPrefs, resultsColumnCatalog } from "./resultsColumns";
import { colsKey } from "./useResultsPrefs";

const MIGRATED = "fa.results.colsMigrated";

const readJson = (key) => {
  try {
    const raw = window.localStorage.getItem(key);
    return raw == null ? null : JSON.parse(raw);
  } catch {
    return null;
  }
};
const writeJson = (key, value) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage blocked: the migration simply runs again next time
  }
};

/**
 * The results grid's column layout (one view, A3-11): `useColumnLayout`
 * over `resultsColumnCatalog` (a per-run group = one entry), per user and
 * per experiment (or per dataset on Compare). The old per-dataset hidden set
 * (`results.cols.<datasetId>`) is read once into an untouched layout.
 *
 * @param {{summary: object, datasetId: string, scopeId: string,
 *   why: boolean, taskSuite: boolean}} args
 */
export default function useResultsColumns({
  summary,
  datasetId,
  scopeId,
  why,
  whyDefault = true,
  taskSuite,
  voice = false,
  scorePin = null,
  primaryKey = null,
  expectedDefault = true,
}) {
  const catalog = useMemo(
    () =>
      resultsColumnCatalog(summary, {
        why,
        whyDefault,
        taskSuite,
        voice,
        scorePin,
        primaryKey,
        expectedDefault,
      }),
    [
      summary,
      why,
      whyDefault,
      taskSuite,
      voice,
      scorePin,
      primaryKey,
      expectedDefault,
    ],
  );
  const layout = useColumnLayout({
    surface: "results",
    scopeId,
    columns: catalog,
  });

  const ready = catalog.length > 0;
  const untouched = Object.keys(layout.prefs ?? {}).length === 0;
  useEffect(() => {
    if (!ready || !untouched || !scopeId || !datasetId) return;
    const done = readJson(MIGRATED) ?? [];
    if (done.includes(scopeId)) return;
    writeJson(MIGRATED, [...done, scopeId].slice(-200));
    const legacy = readJson(colsKey(datasetId));
    if (!Array.isArray(legacy)) return;
    const prefs = legacyHiddenToPrefs(legacy, catalog);
    if (Object.keys(prefs).length) layout.setPrefs(prefs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, scopeId, datasetId]);

  return layout;
}
