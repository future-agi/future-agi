// The experiment stats read (column_config, table_data with saved ranks,
// metadata.is_winner_chosen) behind the Summary's Choose winner action.
// The query key and the raw axios response match the classic Summary, so
// ChooseWinnerDrawer's invalidation of ["experiment-summary", id] after a
// save refreshes every reader of the ranking.
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const experimentStatsKey = (experimentId) => [
  "experiment-summary",
  experimentId,
];

export function useExperimentStats(experimentId, { enabled = true } = {}) {
  return useQuery({
    queryKey: experimentStatsKey(experimentId),
    queryFn: () =>
      axios.get(endpoints.develop.experiment.getSummary(experimentId)),
    enabled: Boolean(experimentId) && enabled,
    select: (response) => response?.data?.result,
  });
}
