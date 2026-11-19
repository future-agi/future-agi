import { useEffect } from "react";
import {
  KB_CHANGE_AFTER_MS,
  kbChangesOf,
} from "src/api/simulate-environments/_fixtures/sources";
import {
  useEnvironmentSources,
  useUpdateSource,
} from "src/api/simulate-environments/sources";
import { isSaved } from "./sources.constants";

/**
 * Stands in for the Knowledge base changing after a copy: any saved
 * Knowledge base copy that read is flagged KB_CHANGE_AFTER_MS after this sees
 * it saved, once per copy (`ui.kb_changed`), so a fresh copy never shows it.
 */
export default function useKnowledgeBaseChanges(envId) {
  const { data: summary } = useEnvironmentSources(envId);
  const updateSource = useUpdateSource(envId);
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
    const timer = setTimeout(
      () =>
        due.split(",").forEach((id) =>
          updateSource(id, (one) => ({
            ...one,
            ui: { ...one.ui, changed_upstream: true, kb_changed: true },
          })),
        ),
      KB_CHANGE_AFTER_MS,
    );
    return () => clearTimeout(timer);
  }, [due, updateSource]);
}
