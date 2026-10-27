import {
  Box,
  Button,
  Collapse,
  Grid,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import React, { useMemo, useState } from "react";
import { useParams } from "react-router";
import useKpis from "src/hooks/useKpis";
import SvgColor from "src/components/svg-color/svg-color.jsx";
import CallDetails from "./CallDetails";
import SystemMetrics from "./SystemMetrics";
import EvaluationMetrics from "./EvaluationMetrics";
import { extractKpis, TestRunLoadingStatus } from "../common";
import { useTestDetailStore, useTestExecutionStore } from "../states";
import { isValidFiltersChanged } from "src/hooks/use-get-validated-filters";
import { useTestDetail } from "src/sections/test-detail/context/TestDetailContext.js";
import { getRandomId } from "src/utils/utils";
import PerformanceSkeleton from "./PerformanceSkeleton";
const STATUS_MAPPER = {
  connected_calls: "completed",
  failed_calls: "failed",
};

const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
  id: getRandomId(),
};

export default function PerformanceMetrics() {
  const { executionId } = useParams();
  const [expanded, setExpanded] = useState(false);
  const [collapse, setCollapse] = useState(false);
  const { getGridApi } = useTestDetail();
  const gridApi = getGridApi();
  const boxRef = React.useRef(null);
  const status = useTestExecutionStore((state) => state.status);
  const { data: kpis, isPending } = useKpis(executionId, {
    refetch: TestRunLoadingStatus.includes(status),
  });
  const { systemMetrics, evalMetrics, callDetails, deterministicEvals } =
    useMemo(() => {
      return extractKpis(kpis, kpis?.agent_type);
    }, [kpis]);

  const { setFilters, filters } = useTestDetailStore();
  const handleSetFilter = (type) => {
    const copy = [...filters];

    // 🟩 CASE 1: total_calls → remove status filters, keep others
    if (type === "total_calls") {
      const otherFilters = copy.filter((f) => f.column_id !== "status");

      const newFilters =
        otherFilters.length === 0 ? [defaultFilter] : otherFilters;

      if (isValidFiltersChanged(filters, newFilters)) {
        setFilters(newFilters);
        gridApi?.onFilterChanged?.();
      } else {
        setFilters(newFilters);
      }
      return;
    }

    // 🟨 CASE 2: mapped status filters (connected_calls, failed_calls, etc.)
    if (STATUS_MAPPER[type]) {
      const targetValue = STATUS_MAPPER[type];

      // Remove empty column filters
      const cleanedFilters = copy.filter((f) => f.column_id !== "");
      const updatedFilters = [...cleanedFilters];

      // Find existing status filter
      const existingStatusIndex = updatedFilters.findIndex(
        (f) => f.column_id === "status",
      );

      if (existingStatusIndex >= 0) {
        // Update existing status filter
        updatedFilters[existingStatusIndex] = {
          ...updatedFilters[existingStatusIndex],
          filter_config: {
            ...updatedFilters[existingStatusIndex].filter_config,
            filter_value: targetValue,
          },
        };
      } else {
        // Append new status filter
        updatedFilters.push({
          id: getRandomId(),
          column_id: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: targetValue,
          },
          _meta: { parentProperty: "status" },
        });
      }

      // Trigger filter update only if something changed
      if (isValidFiltersChanged(filters, updatedFilters)) {
        setFilters(updatedFilters);
        gridApi?.onFilterChanged?.();
      } else {
        setFilters(updatedFilters);
      }

      return;
    }

    setFilters(copy);
  };

  const handleToggleExpand = () => {
    if (expanded && boxRef?.current) {
      const boxHeight = boxRef?.current?.offsetHeight;

      if (boxHeight > 1000) {
        boxRef.current.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }
    }

    setExpanded((prev) => !prev);
  };

  return (
    <Box
      ref={boxRef}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        padding: 1.5,
        bgcolor: "var(--bg-neutral)",
      }}
    >
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <Stack direction={"row"} gap={1} alignItems={"center"}>
          <SvgColor
            sx={{
              height: "18px",
              width: "18px",
              bgcolor: "text.primary",
            }}
            src="/assets/icons/ic_performace.svg"
          />
          <Typography
            typography={"s1_2"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Performance Metrics
          </Typography>
        </Stack>
        <IconButton onClick={() => setCollapse((prev) => !prev)}>
          <SvgColor
            sx={{
              height: "24px",
              width: "24px",
              color: "text.primary",
              rotate: collapse ? "0deg" : "180deg",
              transition: "rotate 0.3s ease-in-out",
            }}
            src="/assets/icons/custom/lucide--chevron-down.svg"
          />
        </IconButton>
      </Stack>
      <Collapse in={!collapse}>
        <Box
          sx={() => ({
            overflow: {
              xs: "auto",
              sm: "hidden",
            },
            maxHeight: expanded ? "100%" : "400px",
            transition: "max-height 0.4s ease-in-out",
          })}
        >
          {isPending && !kpis ? (
            <PerformanceSkeleton />
          ) : (
            <Grid container spacing={2}>
              <Grid item xs={12} md={2.5}>
                <CallDetails
                  handleSetFilter={handleSetFilter}
                  expanded={expanded}
                  data={callDetails}
                  agentType={kpis?.agent_type}
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <SystemMetrics expanded={expanded} data={systemMetrics} />
              </Grid>
              <Grid item xs={12} md={5.5}>
                <EvaluationMetrics
                  expanded={expanded}
                  data={{ evalMetrics, deterministicEvals }}
                  isPending={isPending}
                  status={status}
                />
              </Grid>
            </Grid>
          )}
          <Button
            disabled={isPending}
            onClick={handleToggleExpand}
            variant="outlined"
            size="small"
            sx={{
              ml: "auto",
              display: "flex",
              color: "text.primary",
              typography: "s2",
              fontWeight: "fontWeightMedium",
              borderRadius: 1,
              mt: 1,
              borderColor: "text.disabled",
            }}
            startIcon={
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                  rotate: "180deg",
                }}
                src={
                  expanded
                    ? "/assets/icons/custom/lucide--chevron-down.svg"
                    : "/assets/icons/custom/eye.svg"
                }
              />
            }
          >
            {expanded ? "Minimize" : "View all metrics"}
          </Button>
        </Box>
      </Collapse>
    </Box>
  );
}
