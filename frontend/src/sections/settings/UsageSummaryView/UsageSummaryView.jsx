import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import FormSearchSelectFieldState from "../../../components/FromSearchSelectField/FormSearchSelectFieldState";
import Metrics from "./Metrics";
import TableView from "./TableView";
import {
  convertTotalsToMetrics,
  convertWorkspaceToMetrics,
  getLastThirteenMonths,
  getMonthAndYear,
} from "./common";
import { useQuery } from "@tanstack/react-query";
import { useWorkspacesList } from "src/api/workspaces/list";
import axios from "src/utils/axios";
import _ from "lodash";
import { endpoints } from "../../../utils/axios";

export default function UsageSummaryView({ workspaceId }) {
  const isLockedWorkspace = !!workspaceId;

  const usageMonths = getLastThirteenMonths().map((item) => ({
    ...item,
    label: _.startCase(_.replace(item?.value, /_/g, " ")),
  }));
  const [selectedMonth, setSelectedMonth] = useState(usageMonths?.[0]?.value);
  const [selectedWorkspace, setSelectedWorkspace] = useState("all");

  // The effective workspace: locked prop takes precedence
  const effectiveWorkspace = isLockedWorkspace
    ? workspaceId
    : selectedWorkspace;
  const isAll = !isLockedWorkspace && effectiveWorkspace === "all";

  // Fetch workspace list for dropdown (only needed for org-level view)
  const { data: workspaceList } = useWorkspacesList({
    enabled: !isLockedWorkspace,
  });

  const workspaceOptions = useMemo(() => {
    const options = [{ label: "All", value: "all" }];
    if (workspaceList) {
      workspaceList.forEach((ws) => {
        options.push({
          label: ws.display_name || ws.name,
          value: ws.id,
        });
      });
    }
    return options;
  }, [workspaceList]);

  // Org-level metrics (when "All" is selected)
  const { data: orgData, isLoading: isLoadingOrgMetrics } = useQuery({
    queryKey: ["metrics", selectedMonth],
    queryFn: async () => {
      const res = await axios.get(endpoints.settings.usageMetrics, {
        params: getMonthAndYear(selectedMonth),
      });
      return {
        metrics: res?.data?.result?.totals,
        workspaces: res?.data?.result?.totalWorkspacesCount,
      };
    },
    enabled: !!selectedMonth && isAll,
  });

  // Workspace-level data (when a specific workspace is selected or locked)
  const { data: wsUsageData, isLoading: isLoadingWsMetrics } = useQuery({
    queryKey: ["workspace-usage", selectedMonth, effectiveWorkspace],
    queryFn: async () => {
      const res = await axios.get(endpoints.settings.usageTotals, {
        params: getMonthAndYear(selectedMonth),
      });
      const workspaces = res?.data?.result?.workspaces || [];
      return workspaces.find((ws) => ws.id === effectiveWorkspace) || null;
    },
    enabled: !!selectedMonth && !isAll,
  });

  const isLoadingMetrics = isAll ? isLoadingOrgMetrics : isLoadingWsMetrics;

  const transformedMetricsData = isAll
    ? convertTotalsToMetrics(orgData?.metrics)
    : convertWorkspaceToMetrics(wsUsageData);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Stack gap={0.5}>
        <Typography
          color={"text.primary"}
          typography={"m2"}
          fontWeight={"fontWeightSemiBold"}
        >
          Usage Summary
        </Typography>
        <Typography
          color={"text.primary"}
          typography={"s1"}
          fontWeight={"fontWeightRegular"}
        >
          {isLockedWorkspace
            ? "Workspace usage summary"
            : "Organization-level usage summary for Future AGI"}
        </Typography>
      </Stack>
      <Stack direction={"row"} gap={2}>
        <FormSearchSelectFieldState
          size="small"
          label="Month"
          sx={{
            maxWidth: "250px",
          }}
          onChange={(e) => setSelectedMonth(e?.target?.value)}
          value={selectedMonth}
          options={usageMonths}
          placeholder={"Select month"}
          showClear={false}
        />
        {!isLockedWorkspace && (
          <FormSearchSelectFieldState
            size="small"
            label="Workspace"
            sx={{
              maxWidth: "250px",
            }}
            onChange={(e) => setSelectedWorkspace(e?.target?.value)}
            value={selectedWorkspace}
            options={workspaceOptions}
            placeholder={"Select workspace"}
            showClear={false}
          />
        )}
      </Stack>
      <Metrics
        metrics={transformedMetricsData}
        isLoading={!selectedMonth || isLoadingMetrics}
      />
      <TableView
        isLoadingMetrics={isLoadingMetrics}
        singleWorkSpace={!isAll}
        selectedMonth={selectedMonth}
        totalMetrics={isAll ? orgData?.metrics : null}
        selectedWorkspaceId={isAll ? null : effectiveWorkspace}
      />
    </Box>
  );
}

UsageSummaryView.propTypes = {
  workspaceId: PropTypes.string,
};
