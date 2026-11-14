import PropTypes from "prop-types";
import React, { useCallback, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { SimulationDetailContext } from "./SimulationDetailContext";

export const SimulationDetailProvider = ({ simulationId, children }) => {
  const { id: promptTemplateId } = useParams();
  const queryClient = useQueryClient();
  const gridApiRef = useRef(null);
  const [selectedExecution, setSelectedExecution] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Fetch simulation details
  const {
    data: simulationData,
    isLoading: isLoadingSimulation,
    refetch: refetchSimulation,
  } = useQuery({
    queryKey: ["simulation-detail", promptTemplateId, simulationId],
    queryFn: async () => {
      const res = await axios.get(
        endpoints.promptSimulation.detail(promptTemplateId, simulationId),
      );
      return res.data?.result;
    },
    enabled: !!promptTemplateId && !!simulationId,
  });

  // Fetch executions for this simulation (run test)
  const {
    data: executionsData,
    isLoading: isLoadingExecutions,
    refetch: refetchExecutions,
  } = useQuery({
    queryKey: ["simulation-executions", simulationId, searchQuery],
    queryFn: async () => {
      const res = await axios.get(
        endpoints.runTests.detailExecutions(simulationId),
        {
          params: {
            search: searchQuery || undefined,
          },
        },
      );
      return res.data;
    },
    enabled: !!simulationId,
  });

  const setGridApi = useCallback((api) => {
    gridApiRef.current = api;
  }, []);

  const getGridApi = useCallback(() => {
    return gridApiRef.current;
  }, []);

  const refreshGrid = useCallback(() => {

    queryClient.invalidateQueries({
      queryKey: ["simulation-executions-grid", simulationId],
    });
    queryClient.invalidateQueries({
      queryKey: ["simulation-executions", simulationId],
    });
    if (gridApiRef.current) {
      gridApiRef?.current?.refreshServerSide();
    }
  }, [queryClient, simulationId]);

  return (
    <SimulationDetailContext.Provider
      value={{
        simulationId,
        simulation: simulationData,
        executions: executionsData?.results || [],
        executionsCount: executionsData?.count || 0,
        isLoading: isLoadingSimulation,
        isLoadingExecutions,
        refetchExecutions,
        refetchSimulation,
        selectedExecution,
        setSelectedExecution,
        gridApi: gridApiRef.current,
        setGridApi,
        getGridApi,
        refreshGrid,
        searchQuery,
        setSearchQuery,
      }}
    >
      {children}
    </SimulationDetailContext.Provider>
  );
};

SimulationDetailProvider.propTypes = {
  simulationId: PropTypes.string.isRequired,
  children: PropTypes.node,
};
