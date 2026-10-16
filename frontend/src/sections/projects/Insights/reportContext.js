import { createContext, useCallback, useContext, useMemo } from "react";
import { useTheme } from "@mui/material/styles";
import { buildPreviewIndex, buildRefIndex } from "./insightsCitations";
import { changeByCategory } from "./insightsChanges";
import {
  OTHER_COLOR,
  buildCategoryTree,
  buildColorOrder,
  colorFor,
  isMutedCategory,
  resultTops,
} from "./insightsDistribution";

// What every part of an open report needs: the run, its categories and their
// colours, the citation index, and how to open a category or an example.
const ReportContext = createContext(null);

export const ReportProvider = ReportContext.Provider;

export const useReport = () => useContext(ReportContext);

/** Derived, memoised view of a config and one of its runs. */
export function useReportModel(config, run) {
  const theme = useTheme();
  const mode = theme.palette.mode;
  const result = run?.result;
  const categories = useMemo(() => result?.categories || [], [result]);
  const categoriesById = useMemo(
    () => new Map(categories.map((c) => [c.id, c])),
    [categories],
  );
  const tree = useMemo(() => buildCategoryTree(categories), [categories]);
  const order = useMemo(
    () =>
      buildColorOrder({
        taxonomy: config?.taxonomy,
        runs: config?.runs || [],
        extra: resultTops(categories),
      }),
    [config, categories],
  );
  const otherColor = OTHER_COLOR[mode] || OTHER_COLOR.light;
  const colorOf = useCallback(
    (id) => {
      const category = categoriesById.get(id);
      const topId = category?.level === 2 ? category.parent_id : id;
      const top = categoriesById.get(topId) || category;
      if (top && isMutedCategory(top)) return otherColor;
      return colorFor(topId, order, mode) || otherColor;
    },
    [categoriesById, order, mode, otherColor],
  );
  const refIndex = useMemo(
    () => buildRefIndex(result?.summary),
    [result?.summary],
  );
  const previews = useMemo(
    () => buildPreviewIndex(result?.summary),
    [result?.summary],
  );
  const changes = useMemo(
    () => changeByCategory(result?.changes),
    [result?.changes],
  );
  return {
    mode,
    categories,
    categoriesById,
    tree,
    order,
    colorOf,
    refIndex,
    previews,
    changes,
  };
}
