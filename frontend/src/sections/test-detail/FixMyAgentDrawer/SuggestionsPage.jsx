import { Box } from "@mui/material";
import React, { useState } from "react";
import FixMyAgentHeader from "./FixMyAgentHeader";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../components/show";
import FixMyAgentLoading from "./FixMyAgentLoading";
import FixMyAgentSections from "./FixMyAgentSections";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { kpisQueryOptions } from "src/hooks/useKpis";
import { FixMyAgentRefetchStates } from "./common";
import { DEBUG_FAILURES_REFUSALS } from "../debugFailuresRule";

// The server refuses to start an analysis of a run that Debug failures' rule
// blocks. The refusal is an answer, not a failure: it shows as its reason and
// is not retried or toasted. Any other error is thrown on as before.
const refusalOf = (error) => {
  const refusal = DEBUG_FAILURES_REFUSALS[error?.code];
  if (!refusal) throw error;
  return { refusal };
};

const SuggestionsPage = ({ onClose }) => {
  const { executionId } = useParams();
  const queryClient = useQueryClient();
  // A refused Refresh keeps the analysis the run already has.
  const [refreshRefusal, setRefreshRefusal] = useState(null);
  // The page's counts let the request through, so they were stale: read them again.
  const refused = (error) => {
    const result = refusalOf(error);
    queryClient.invalidateQueries({
      queryKey: kpisQueryOptions(executionId).queryKey,
    });
    return result;
  };

  const {
    data,
    isRefetching,
    isLoading: isLoadingOptimizerAnalysis,
    refetch,
  } = useQuery({
    queryKey: ["optimizer-analysis", executionId],
    queryFn: () =>
      axios
        .get(endpoints.testExecutions.getOptimizerAnalysis(executionId))
        .then((res) => res.data?.result)
        .catch(refused),
    enabled: !!executionId,
    refetchInterval: ({ state }) => {
      if (FixMyAgentRefetchStates.includes(state?.data?.status)) {
        return 5000;
      }
      return false;
    },
    refetchOnWindowFocus: false,
  });
  const optimizerAnalysis = data?.refusal ? undefined : data;
  const refusal = data?.refusal ?? refreshRefusal;

  const {
    mutate: refreshOptimizerAnalysis,
    isPending: isRefetchingOptimizerAnalysis,
  } = useMutation({
    mutationFn: () =>
      axios
        .post(endpoints.testExecutions.refreshOptimizerAnalysis(executionId))
        .then(() => null)
        .catch(refused),
    onSuccess: (result) => {
      if (result?.refusal) {
        setRefreshRefusal(result.refusal);
        return;
      }
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
          // A refused run gets the same refusal again, so it offers no retry.
          refetch={refusal ? undefined : refreshOptimizerAnalysis}
          optimizerAnalysis={optimizerAnalysis}
          refusal={refusal}
        />
      </ShowComponent>
    </Box>
  );
};

SuggestionsPage.propTypes = {
  onClose: PropTypes.func,
};

export default SuggestionsPage;
