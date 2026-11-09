// Ids reserved for a draft (R4 lane Ra, persona fix P1): Level 3 shows the
// complete script before anything is created, so the experiment id and
// each run id are reserved when the drawer opens (`#4A · id reserved for
// this draft`). The name-suggestion endpoint answers them additively
// (`reserved: {experiment_id, run_ids: {A: …}}`).
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export function useReservedIds(datasetId, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["experiment-reserved-ids", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.suggestName(datasetId)),
    select: (res) => {
      const r = res?.data?.result ?? {};
      return {
        ordinal: r.next_ordinal ?? null,
        experimentId: r.reserved?.experiment_id ?? null,
        runIds: r.reserved?.run_ids ?? {},
      };
    },
    enabled: Boolean(datasetId) && enabled,
    staleTime: 0,
  });
}
