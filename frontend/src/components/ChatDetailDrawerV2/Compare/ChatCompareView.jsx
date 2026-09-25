import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { transformToConversations } from "src/sections/test-detail/TestDetailDrawer/BasLineCompare/common";
import CompareHeaderBar from "src/components/BaselineCompare/CompareHeaderBar";
import CompareMetrics from "src/components/BaselineCompare/CompareMetrics";
import CompareScenarioSummary from "src/components/BaselineCompare/CompareScenarioSummary";
import CompareTranscript from "src/components/BaselineCompare/CompareTranscript";

const ChatCompareView = ({ data, onBack }) => {
  const callExecutionId = data?.id;

  const { data: compareData, isLoading } = useQuery({
    queryKey: ["chat-baseline-vs-replay", callExecutionId],
    queryFn: () =>
      axios.get(endpoints.testExecutions.compareExecutions(callExecutionId)),
    select: (response) => response?.data?.result,
    enabled: !!callExecutionId,
  });

  const transcripts = useMemo(() => {
    if (!compareData?.comparison_transcripts) return null;
    return transformToConversations(compareData.comparison_transcripts);
  }, [compareData?.comparison_transcripts]);

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        width: "100%",
        overflow: "auto",
      }}
    >
      <CompareHeaderBar
        onBack={onBack}
        scenarioName={data?.scenario}
        sessionId={data?.session_id ?? data?.sessionId}
        backLabel="Back to chat"
      />

      <Stack gap={2} sx={{ p: 1.5 }}>
        <CompareMetrics
          data={compareData?.comparison_metrics}
          isLoading={isLoading}
          simulationCallType={data?.simulation_call_type}
        />

        <CompareScenarioSummary data={data} />

        <CompareTranscript data={transcripts} isLoading={isLoading} />
      </Stack>
    </Box>
  );
};

ChatCompareView.propTypes = {
  data: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
};

export default ChatCompareView;
