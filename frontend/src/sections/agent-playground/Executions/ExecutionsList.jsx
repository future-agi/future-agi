import React, { useCallback, useEffect } from "react";
import { Box, Typography, Skeleton, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { format, isValid } from "date-fns";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { EXECUTION_STATUS } from "../utils/workflowExecution";

const STATUS_COLORS = {
  [EXECUTION_STATUS.SUCCESS]: "success.main",
  [EXECUTION_STATUS.RUNNING]: "info.main",
  [EXECUTION_STATUS.PENDING]: "warning.main",
  [EXECUTION_STATUS.ERROR]: "error.main",
  [EXECUTION_STATUS.FAILED]: "error.main",
  [EXECUTION_STATUS.SKIPPED]: "error.main",
};

const STATUS_LABELS = {
  [EXECUTION_STATUS.SUCCESS]: "Success",
  [EXECUTION_STATUS.RUNNING]: "Running",
  [EXECUTION_STATUS.PENDING]: "Pending",
  [EXECUTION_STATUS.ERROR]: "Error",
  [EXECUTION_STATUS.FAILED]: "Failed",
  [EXECUTION_STATUS.SKIPPED]: "Skipped",
};

const ExecutionSkeleton = () => (
  <Box
    sx={{ px: 2, py: 1.5, display: "flex", flexDirection: "column", gap: 0.5 }}
  >
    <Skeleton variant="text" width="70%" height={20} />
    <Skeleton variant="text" width="40%" height={16} />
  </Box>
);

const ExecutionsList = ({
  executions,
  selectedExecutionId,
  onExecutionChange,
  isFetchingNextPage,
  fetchNextPage,
  hasNextPage,
}) => {
  const theme = useTheme();

  const fetchNext = useCallback(() => {
    if (hasNextPage && !isFetchingNextPage) {
      fetchNextPage();
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const scrollContainerRef = useScrollEnd(fetchNext, [fetchNext]);

  // Auto-select top execution whenever the newest item changes
  const firstExecutionId = executions[0]?.id;
  useEffect(() => {
    if (firstExecutionId) {
      onExecutionChange(firstExecutionId);
    }
  }, [firstExecutionId, onExecutionChange]);

  return (
    <Box
      ref={scrollContainerRef}
      sx={{
        flex: 1,
        overflowY: "auto",
        minWidth: "200px",
        "&::-webkit-scrollbar": { width: "6px" },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: "rgba(0, 0, 0, 0.3)",
          borderRadius: "3px",
        },
        "&::-webkit-scrollbar-track": { backgroundColor: "transparent" },
      }}
    >
      {executions.map((execution) => {
        const isSelected = execution.id === selectedExecutionId;
        const statusColor = STATUS_COLORS[execution.status] || "text.disabled";
        const statusLabel = STATUS_LABELS[execution.status] || execution.status;
        const startedDate = new Date(execution.startedAt);
        const dateStr = isValid(startedDate)
          ? format(startedDate, "MMM dd, yyyy, h:mm a")
          : "—";

        return (
          <Box
            key={execution.id}
            onClick={() => onExecutionChange(execution.id)}
            sx={{
              px: 2,
              py: 1.5,
              cursor: "pointer",
              borderLeft: isSelected
                ? `3px solid ${theme.palette.primary.main}`
                : "3px solid transparent",
              bgcolor: isSelected ? "action.selected" : "transparent",
              "&:hover": {
                bgcolor: isSelected ? "action.selected" : "action.hover",
              },
            }}
          >
            <Typography
              typography="s2_1"
              fontWeight="fontWeightMedium"
              color="text.primary"
              noWrap
            >
              {dateStr}
            </Typography>
            <Box
              sx={{ display: "flex", alignItems: "center", gap: 0.75, mt: 0.5 }}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  bgcolor: statusColor,
                  flexShrink: 0,
                }}
              />
              <Typography typography="s3" color="text.secondary">
                {statusLabel}
              </Typography>
            </Box>
          </Box>
        );
      })}

      {isFetchingNextPage && (
        <>
          {[...Array(3)].map((_, i) => (
            <ExecutionSkeleton key={`next-skeleton-${i}`} />
          ))}
        </>
      )}
    </Box>
  );
};

ExecutionsList.propTypes = {
  executions: PropTypes.array.isRequired,
  selectedExecutionId: PropTypes.string,
  onExecutionChange: PropTypes.func.isRequired,
  isFetchingNextPage: PropTypes.bool,
  fetchNextPage: PropTypes.func,
  hasNextPage: PropTypes.bool,
};

export default ExecutionsList;

export { STATUS_LABELS, STATUS_COLORS };
