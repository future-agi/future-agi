import { Box, Typography, Tabs, Tab, useTheme } from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import InsightEvals from "./insight-evals";
import InsightActions from "./insight-actions";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "src/routes/hooks";
import axios, { endpoints } from "src/utils/axios";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { ShowComponent } from "../show";

const RunInsights = ({ setSelectedTraceIds }) => {
  const [selectedTab, setSelectedTab] = useState(0);

  const { runId } = useParams();

  const { data, isLoading } = useQuery({
    queryKey: ["run-insights", runId],
    queryFn: () =>
      axios.get(endpoints.project.getProjectVersionInsight(), {
        params: { project_version_id: runId },
      }),
    select: (data) => data.data?.result,
  });

  const handleTabChange = (event, newValue) => {
    setSelectedTab(newValue);
    trackEvent(Events.actionsEvalsToggle);
  };

  const theme = useTheme();
  const systemMetrics = data?.systemMetrics || data?.system_metrics || {};
  const evalMetrics = data?.evalMetrics || data?.eval_metrics || {};
  const avgLatencyMs = getMetricValue(
    systemMetrics,
    "avgLatencyMs",
    "avg_latency_ms",
  );
  const avgCost = getMetricValue(systemMetrics, "avgCost", "avg_cost");
  const avgTokens = getMetricValue(systemMetrics, "avgTokens", "avg_tokens");

  const values = {
    "Average Latency":
      avgLatencyMs === null ? "N/A" : `${avgLatencyMs.toLocaleString()} ms`,
    "Avg. Cost": avgCost === null ? "N/A" : `$${avgCost}`,
    "Avg. Tokens": avgTokens === null ? "N/A" : `${avgTokens}`,
  };

  if (isLoading) return <></>;

  return (
    <Box
      sx={{
        py: (theme) => theme.spacing(2),
        display: "flex",
        flexDirection: "column",
        height: "100vh",
      }}
    >
      <Typography
        variant="body1"
        color="text.primary"
        fontWeight="fontWeightMedium"
        sx={{
          marginLeft: (theme) => theme.spacing(2),
          mt: (theme) => theme.spacing(1),
        }}
      >
        Run Insights
      </Typography>
      <Box
        sx={{
          marginTop: (theme) => theme.spacing(2),
          p: (theme) => theme.spacing(1),
        }}
      >
        {Object.entries(values).map(([key, value]) => (
          <Box
            key={key}
            sx={{
              px: (theme) => theme.spacing(1),
              py: (theme) => theme.spacing(0.8),
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <Typography
              component="span"
              fontSize="s1"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
              sx={{ flex: 1 }}
            >
              {key}:
            </Typography>
            <Typography
              component="span"
              fontSize="s1"
              sx={{
                fontWeight: "fontWeightRegular",
                flex: 1,
              }}
            >
              {value}
            </Typography>
          </Box>
        ))}
      </Box>

      {/* Tab menu */}
      <Box
        sx={{
          px: (theme) => theme.spacing(2),
        }}
      >
        <Box
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Tabs
            value={selectedTab}
            onChange={handleTabChange}
            indicatorColor="primary"
            textColor="primary"
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
              },
            }}
            sx={{
              minHeight: 0,
              "& .MuiTab-root": {
                margin: "0 !important",
                fontWeight: "600",
                typography: "s1",
                color: "primary.main",
                "&:not(.Mui-selected)": {
                  color: "text.disabled",
                  fontWeight: "500",
                },
              },
            }}
          >
            <Tab
              label="Actions"
              sx={{
                margin: theme.spacing(0),
                px: theme.spacing(1.875),
              }}
            />
            <Tab
              label="Evals"
              sx={{
                margin: theme.spacing(0),
                px: theme.spacing(1.875),
              }}
            />
          </Tabs>
        </Box>
      </Box>

      {/* Tab content */}

      <Box
        sx={{
          px: (theme) => theme.spacing(2),
          overflowY: "auto",
        }}
      >
        <Box
          sx={{
            flex: 1,
          }}
        >
          <ShowComponent condition={selectedTab === 0}>
            <Typography variant="body1">
              <InsightActions
                evalMetrics={evalMetrics}
                setSelectedTraceIds={setSelectedTraceIds}
              />
            </Typography>
          </ShowComponent>
          <ShowComponent condition={selectedTab === 1}>
            <Typography variant="body1">
              <InsightEvals evalMetrics={evalMetrics} />
            </Typography>
          </ShowComponent>
        </Box>
      </Box>
    </Box>
  );
};

RunInsights.propTypes = {
  setSelectedTraceIds: PropTypes.func,
};

function getMetricValue(metrics, camelKey, snakeKey) {
  return metrics?.[camelKey] ?? metrics?.[snakeKey] ?? null;
}

export default RunInsights;
