import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  KB_CHANGE_AFTER_MS,
  kbChangesOf,
} from "src/api/simulate-environments/_fixtures/sources";
import {
  sourceSummaryKey,
  useEnvironmentSources,
} from "src/api/simulate-environments/sources";
import { isSaved } from "./sources.constants";

/**
 * Stands in for the Knowledge base changing after a copy: a saved copy of a
 * file with sample changes is flagged KB_CHANGE_AFTER_MS after this sees it
 * saved, once per copy (`ui.kb_changed`), so a fresh copy never shows it.
 */
export default function useKnowledgeBaseChanges(envId) {
  const queryClient = useQueryClient();
  const { data: summary } = useEnvironmentSources(envId);
  const due = (summary?.sources || [])
    .filter(
      (source) =>
        isSaved(source) &&
        !source.ui?.removed &&
        !source.ui?.kb_changed &&
        kbChangesOf(source).length > 0,
    )
    .map((source) => source.source_id)
    .join(",");

  useEffect(() => {
    if (!due) return undefined;
    const ids = due.split(",");
    const timer = setTimeout(
      () =>
        queryClient.setQueryData(sourceSummaryKey(envId), (current) =>
          current
            ? {
                ...current,
                sources: current.sources.map((one) =>
                  ids.includes(one.source_id)
                    ? {
                        ...one,
                        ui: {
                          ...one.ui,
                          changed_upstream: true,
                          kb_changed: true,
                        },
                      }
                    : one,
                ),
              }
            : current,
        ),
      KB_CHANGE_AFTER_MS,
    );
    return () => clearTimeout(timer);
  }, [due, envId, queryClient]);
}
