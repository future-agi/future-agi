import { useCallback } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { parseVersionParam } from "src/api/develop/dataset-versions";
import { useAsOfViewStore } from "./versionedSource";

/**
 * URL state of the Versions UI on the dataset page (PRODUCT-SPEC §2.3):
 *   history=1     the History panel is docked (it shares the right slot
 *                 with the Row view; `row` stays in the URL)
 *   version=<n>   the as-of view of version n (read-only)
 * Opening a past version pushes a history entry (Back returns to where you
 * were); panel toggles and Back to latest replace.
 */
export default function useVersionUrl() {
  const { dataset: datasetId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const historyOpen = searchParams.get("history") === "1";
  const asOfNumber = parseVersionParam(searchParams.get("version"));

  const update = useCallback(
    (mutate, { push = false } = {}) => {
      const next = new URLSearchParams(window.location.search);
      mutate(next);
      setSearchParams(next, { replace: !push });
    },
    [setSearchParams],
  );

  const setHistoryOpen = useCallback(
    (open) =>
      update((p) => {
        if (open) {
          p.set("tab", "data");
          p.set("history", "1");
        } else {
          p.delete("history");
          // R3 (P2): the history filter goes with the panel.
          p.delete("hq");
          p.delete("hchips");
          p.delete("hcmp");
        }
      }),
    [update],
  );

  /** `null` (or the latest number) returns to latest. */
  const viewVersion = useCallback(
    (number, { latestNumber } = {}) => {
      useAsOfViewStore.getState().resetView(datasetId);
      const toLatest = number == null || number === latestNumber;
      update(
        (p) => {
          p.set("tab", "data");
          if (toLatest) p.delete("version");
          else p.set("version", String(number));
        },
        { push: !toLatest },
      );
    },
    [update, datasetId],
  );

  /** The version chip's `Compare two versions…`: the panel, in compare mode. */
  const openCompare = useCallback(
    () =>
      update((p) => {
        p.set("tab", "data");
        p.set("history", "1");
        p.set("hcmp", "1");
      }),
    [update],
  );

  return {
    datasetId,
    historyOpen,
    asOfNumber,
    setHistoryOpen,
    openCompare,
    toggleHistory: () => setHistoryOpen(!historyOpen),
    viewVersion,
    update,
  };
}
