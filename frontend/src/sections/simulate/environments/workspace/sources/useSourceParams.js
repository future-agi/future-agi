import { useSearchParams } from "react-router-dom";

const SOURCE_PARAMS = ["source", "sourceTab", "at", "show"];

/**
 * The open file drawer, kept in the URL: `?source=<id>`, plus `sourceTab` for
 * one of its tabs (the workspace keeps its own tab in `?tab=`) and `at` for a
 * cited passage or row. `openSource()` without an id just shows World & tools;
 * `showSources()` also brings the Sources card into view (`show=sources`).
 */
export default function useSourceParams() {
  const [params, setParams] = useSearchParams();

  const write = (set) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        SOURCE_PARAMS.forEach((key) => next.delete(key));
        set(next);
        return next;
      },
      { replace: true },
    );

  const openSource = (id, tab, at) =>
    write((next) => {
      // The file drawer only opens on the World & tools tab.
      next.set("tab", "world");
      if (id) next.set("source", id);
      if (tab) next.set("sourceTab", tab);
      if (at) next.set("at", at);
    });

  const showSources = () =>
    write((next) => {
      next.set("tab", "world");
      next.set("show", "sources");
    });

  return {
    sourceId: params.get("source"),
    showing: params.get("show"),
    showSources,
    sourceTab: params.get("sourceTab"),
    at: params.get("at"),
    openSource,
    closeSource: () => write(() => {}),
  };
}
