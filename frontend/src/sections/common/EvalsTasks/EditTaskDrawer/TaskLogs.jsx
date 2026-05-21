import { Box, Typography, Chip, CircularProgress } from "@mui/material";
import { alpha } from "@mui/material/styles";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { ShowComponent } from "src/components/show";
import { format } from "date-fns";

const KeyValueOrChip = ({ label, value }) => {
  const chipStyle = {
    backgroundColor:
      value > 0
        ? label === "Success"
          ? "green"
          : label === "Errors"
            ? "#DB2F2D00"
            : "gray"
        : "transparent",
    borderColor:
      value > 0
        ? label === "Success"
          ? "green"
          : label === "Errors"
            ? "#DB2F2D00"
            : "gray"
        : "transparent",
    borderWidth: "1px",
    borderStyle: "solid",
    color: "text.primary",

    "&:hover": {
      backgroundColor:
        value > 0
          ? label === "Success"
            ? "rgba(0, 128, 0, 0.8)"
            : label === "Errors"
              ? "#DB2F2D00"
              : "rgba(128, 128, 128, 0.8)"
          : "transparent",
      borderColor:
        value > 0
          ? label === "Success"
            ? "rgba(0, 128, 0, 0.8)"
            : label === "Errors"
              ? "#DB2F2D00"
              : "rgba(128, 128, 128, 0.8)"
          : "transparent",
    },
  };

  return (
    <Box sx={{ display: "flex", gap: 2, minWidth: "150px" }}>
      {value > 0 ? (
        <Chip
          label={`${label}: ${value}`}
          color="default"
          size="small"
          sx={chipStyle}
        />
      ) : (
        <Typography variant="body1">{`${label}: ${value}`}</Typography>
      )}
    </Box>
  );
};

KeyValueOrChip.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
};

const TaskLogs = ({ evalTaskId }) => {
  const { data, isLoading } = useQuery({
    queryKey: ["eval-task-logs", evalTaskId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskLogs(), {
        params: { eval_task_id: evalTaskId },
      }),
    select: (data) => data?.data?.result,
    enabled: !!evalTaskId,
  });

  const truncateErrorLog = (log, length = 320) => {
    if (log.length > length) {
      return log.substring(0, length) + "...";
    }
    return log;
  };
  const warningGroups = data?.warning_groups || data?.warningGroups || [];
  const warningsCount = data?.warnings_count ?? data?.warningsCount ?? 0;

  return (
    <Box
      sx={{
        overflow: "auto",
        // minWidth: "40vw", // Slightly wider container
      }}
    >
      <ShowComponent condition={isLoading}>
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: (theme) => alpha(theme.palette.background.paper, 0.8),
            zIndex: 1,
          }}
        >
          <CircularProgress />
        </Box>
      </ShowComponent>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          overflow: "auto",
          gap: 1,
        }}
      >
        {/* First Row */}
        <Box
          sx={{
            display: "flex",
            my: 2,
          }}
        >
          <Typography fontSize="14px" minWidth={"200px"}>
            Success: {data?.success_count ?? data?.successCount}
          </Typography>
          <Typography fontSize="14px">
            Task Run:{" "}
            {(() => {
              const startTime = data?.start_time ?? data?.startTime;
              const endTime = data?.end_time ?? data?.endTime;
              if (!startTime || !endTime) return "";
              return `${format(new Date(startTime), "MM/dd/yy h:mma")} - ${format(new Date(endTime), "MM/dd/yy h:mma")}`;
            })()}
          </Typography>
        </Box>
        {/* Second Row */}
        <Box sx={{ display: "flex", mb: 2 }}>
          <Typography fontSize="14px" minWidth={"200px"}>
            Skips: 0
          </Typography>
          <Typography fontSize="14px">
            Errors: {data?.errors_count ?? data?.errorsCount}
          </Typography>
        </Box>
        {warningsCount > 0 && (
          <Box
            sx={(theme) => ({
              p: 1.5,
              mb: 2,
              borderRadius: 1,
              display: "flex",
              flexDirection: "column",
              gap: 1,
              bgcolor: alpha(theme.palette.warning.main, 0.08),
              border: "1px solid",
              borderColor: alpha(theme.palette.warning.main, 0.18),
            })}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Iconify
                icon="solar:danger-triangle-linear"
                width={18}
                color="warning.main"
              />
              <Typography fontSize="14px" fontWeight={600}>
                Partial Inputs: {warningsCount}
              </Typography>
            </Box>
            {warningGroups.map((group) => (
              <Box
                key={`${group.type}-${(group.empty_keys || []).join(",")}`}
                sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}
              >
                {(group.empty_keys || []).map((key) => (
                  <Chip
                    key={key}
                    label={`Missing: ${key}`}
                    color="warning"
                    variant="outlined"
                    size="small"
                    sx={{ fontSize: "10px", height: 18 }}
                  />
                ))}
                <Typography variant="caption" color="text.secondary">
                  {group.count} occurrence{group.count === 1 ? "" : "s"}
                </Typography>
              </Box>
            ))}
          </Box>
        )}
        {/* Error Log Box */}
        <ShowComponent
          condition={
            (data?.errors_message ?? data?.errorsMessage) &&
            (data?.errors_message ?? data?.errorsMessage)?.length > 0
          }
        >
          <Box
            sx={{
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 1,
              // flex: 1,
            }}
          >
            {(data?.errors_message ?? data?.errorsMessage)?.map(
              (error, index) => (
                <Box
                  key={index}
                  sx={
                    {
                      // paddingX: 4,
                    }
                  }
                >
                  <Box
                    sx={{
                      padding: 2,
                      borderRadius: 1,
                      display: "flex",
                      maxWidth: "100%",
                      justifyContent: "center",
                      bgcolor: (theme) => alpha(theme.palette.error.main, 0.08),
                      border: "1px solid",
                      borderColor: (theme) =>
                        alpha(theme.palette.error.main, 0.16),
                      gap: 1,
                    }}
                  >
                    {/* Icon */}
                    <Iconify
                      icon="material-symbols:warning-outline"
                      width={20}
                      color="error.main"
                    />
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ maxWidth: "90%" }}
                    >
                      {truncateErrorLog(error)}
                    </Typography>
                  </Box>
                </Box>
              ),
            )}
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

TaskLogs.propTypes = {
  evalTaskId: PropTypes.string,
};

export default TaskLogs;
