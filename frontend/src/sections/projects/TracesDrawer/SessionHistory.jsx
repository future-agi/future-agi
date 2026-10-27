import React from "react";
import { Box, Stack } from "@mui/material";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";

import ShowTrace from "./ShowTrace";
import SessionSkeleton from "./SessionSkeleton";

const SessionHistory = ({
  traceDetail,
  loading,
  isFetchingNextPage,
  activeSessionId,
  onTraceClick,
}) => {
  const interactions =
    traceDetail?.map((trace) => ({
      traceId: trace.trace_id,
      Human: trace.input,
      AI: trace.output,
      systemMetrics: trace.system_metrics,
      evaluationMetrics: trace.evals_metrics,
      traceData: { traceId: trace.trace_id },
    })) || [];

  return (
    <Box
      sx={{
        flex: 1,
        minWidth: 0,
        maxWidth: "100%",
      }}
    >
      <ShowComponent condition={loading}>
        <SessionSkeleton />
        <SessionSkeleton />
      </ShowComponent>

      <Stack
        direction={"column"}
        gap={2}
        sx={{ minWidth: 0, maxWidth: "100%" }}
      >
        {interactions.map((interaction, index) => (
          <ShowTrace
            key={index}
            traceId={interaction.traceId}
            human={interaction.Human}
            ai={interaction.AI}
            systemMetrics={interaction.systemMetrics}
            evaluationMetrics={interaction.evaluationMetrics}
            activeSessionId={activeSessionId}
            onTraceClick={onTraceClick}
          />
        ))}
      </Stack>
      <ShowComponent condition={isFetchingNextPage}>
        <SessionSkeleton />
      </ShowComponent>
    </Box>
  );
};

SessionHistory.propTypes = {
  traceDetail: PropTypes.object,
  loading: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  activeSessionId: PropTypes.string,
  onTraceClick: PropTypes.func,
};

export default SessionHistory;
