import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";

export const useRefreshScenarioGrid = (scenarioId) => {
  const queryClient = useQueryClient();
  const { refreshGrid } = useDevelopDetailContext();

  return useCallback(() => {
    if (scenarioId) {
      queryClient.invalidateQueries({
        queryKey: ["scenario-detail", scenarioId],
      });
    }
    refreshGrid();
  }, [queryClient, refreshGrid, scenarioId]);
};
