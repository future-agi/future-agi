import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { transformToConversations } from "src/sections/test-detail/TestDetailDrawer/BasLineCompare/common";

import CompareHeaderBar from "./CompareHeaderBar";
import ChatCompareMetrics from "./ChatCompareMetrics";
import ChatCompareTranscript from "./ChatCompareTranscript";
import CompareScenarioSummary from "./CompareScenarioSummary";

// Top-level chat baseline-vs-replay view, rendered inside the ChatDetailDrawerV2 shell.
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
    if (!compareData?.comparisonTranscripts) return null;
    return transformToConversations(compareData.comparisonTranscripts);
  }, [compareData?.comparisonTranscripts]);

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
      />

      <Stack gap={2} sx={{ p: 1.5 }}>
        <ChatCompareMetrics
          data={compareData?.comparison_metrics}
          isLoading={isLoading}
        />

        {/* Compact scenario summary scoped to the compare view. The
            legacy `TestDetailDrawerScenarioTable` (still used by voice
            compare) is intentionally chunkier with nested borders and
            fixed-height columns; we use a denser layout here so the
            scenario block reads as a peer of the KPI strip rather
            than dominating the page. */}
        <CompareScenarioSummary data={data} />

        <ChatCompareTranscript data={transcripts} isLoading={isLoading} />
      </Stack>
    </Box>
  );
};

ChatCompareView.propTypes = {
  data: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
};

export default ChatCompareView;
