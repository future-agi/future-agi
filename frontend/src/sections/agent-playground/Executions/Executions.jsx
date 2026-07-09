import React, { useState, useCallback, useMemo } from "react";
import {
  Box,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import { useParams } from "react-router-dom";
import { useGetExecutions } from "src/api/agent-playground/agent-playground";
import ExecutionsList from "./ExecutionsList";
import ExecutionDetailView from "./ExecutionDetailView";

export default function Executions() {
  const { agentId } = useParams();
  const [selectedExecutionId, setSelectedExecutionId] = useState(null);

  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } =
    useGetExecutions(agentId);

  const executions = useMemo(
    () =>
      (data?.pages ?? []).flatMap((page) =>
        (page.data?.result?.executions ?? []).map((e) => ({
          id: e.id,
          status: e.status?.toLowerCase(),
          startedAt: e.started_at,
          completedAt: e.completed_at,
        })),
      ),
    [data],
  );

  const handleExecutionChange = useCallback((executionId) => {
    setSelectedExecutionId(executionId);
  }, []);

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (executions.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Typography typography="m3" color="text.disabled">
          No executions yet
        </Typography>
        <Typography typography="s2" color="text.secondary">
          Run your workflow from the Agent Builder to see results here
        </Typography>
      </Box>
    );
  }

  return (
    <Stack direction="row" height="100%">
      <Box
        sx={{
          width: "230px",
          flexShrink: 0,
          height: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <ExecutionsList
          executions={executions}
          selectedExecutionId={selectedExecutionId}
          onExecutionChange={handleExecutionChange}
          isFetchingNextPage={isFetchingNextPage}
          fetchNextPage={fetchNextPage}
          hasNextPage={hasNextPage}
        />
      </Box>
      <Divider orientation="vertical" />
      <ExecutionDetailView
        graphId={agentId}
        executionId={selectedExecutionId}
      />
    </Stack>
  );
}
