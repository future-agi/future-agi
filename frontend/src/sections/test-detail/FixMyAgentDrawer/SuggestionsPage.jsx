import { Box } from "@mui/material";
import React from "react";
import FixMyAgentHeader from "./FixMyAgentHeader";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../components/show";
import FixMyAgentLoading from "./FixMyAgentLoading";
import FixMyAgentSections from "./FixMyAgentSections";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { FixMyAgentRefetchStates } from "./common";

const SuggestionsPage = ({ onClose }) => {
  const { executionId } = useParams();

  const {
    data: optimizerAnalysis,
    isRefetching,
    isLoading: isLoadingOptimizerAnalysis,
    refetch,
  } = useQuery({
    queryKey: ["optimizer-analysis", executionId],
    queryFn: () =>
      axios.get(endpoints.testExecutions.getOptimizerAnalysis(executionId)),
    enabled: !!executionId,
    select: (data) => data?.data?.result,
    refetchInterval: ({ state }) => {
      const result = state?.data?.data?.result;
      if (FixMyAgentRefetchStates.includes(result?.status)) {
        return 5000;
      }
      return false;
    },
    refetchOnWindowFocus: false,
  });

  const {
    mutate: refreshOptimizerAnalysis,
    isPending: isRefetchingOptimizerAnalysis,
  } = useMutation({
    mutationFn: () =>
      axios.post(
        endpoints.testExecutions.refreshOptimizerAnalysis(executionId),
      ),
    onSuccess: () => {
      refetch();
    },
  });

  const isLoadingData =
    isLoadingOptimizerAnalysis ||
    isRefetching ||
    isRefetchingOptimizerAnalysis ||
    FixMyAgentRefetchStates.includes(optimizerAnalysis?.status);

  return (
    <Box
      sx={{
        width: "570px",
        padding: 2,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <FixMyAgentHeader onClose={onClose} />
      <ShowComponent condition={isLoadingData}>
        <FixMyAgentLoading />
      </ShowComponent>
      <ShowComponent condition={!isLoadingData}>
        <FixMyAgentSections
          refetch={refreshOptimizerAnalysis}
          optimizerAnalysis={optimizerAnalysis}
        />
      </ShowComponent>
    </Box>
  );
};

SuggestionsPage.propTypes = {
  onClose: PropTypes.func,
};

export default SuggestionsPage;
