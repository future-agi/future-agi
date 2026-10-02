import { Box, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import React from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import PerformanceSkeleton from "./Skeletons/PerformanceSkeleton";
import { ShowComponent } from "src/components/show";

const PerformanceCard = ({ icon, value, caption }) => {
  return (
    <Box
      sx={{
        backgroundColor: "background.default",
        borderRadius: 1,
        display: "flex",
        flexDirection: "column",
        flex: 1,
        padding: 2,
        paddingY: 1.5,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {icon}
      <Typography typography="m2" fontWeight={500}>
        {value}
      </Typography>
      <Typography color="text.disabled" fontWeight={600} typography="s3">
        {caption}
      </Typography>
    </Box>
  );
};

PerformanceCard.propTypes = {
  icon: PropTypes.node,
  value: PropTypes.string,
  caption: PropTypes.string,
};

const colorMap = (val) => {
  if (val < 5) return "red";
  if (val < 7) return "orange";
  return "green";
};

const PerformanceDetails = () => {
  const { executionId } = useParams();

  const { data, isPending } = useQuery({
    queryKey: ["test-execution-performance", executionId],
    queryFn: () =>
      axios.get(
        endpoints.testExecutions.executionPerformanceSummary(executionId),
      ),
    select: (data) => data.data,
  });

  return (
    <Box
      sx={{
        flex: 1,
        paddingX: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        paddingBottom: 2,
        overflow: "auto",
        paddingTop: 1,
        zIndex: 2,
        backgroundColor: "background.paper",
      }}
    >
      <ShowComponent condition={isPending}>
        <PerformanceSkeleton />
      </ShowComponent>
      <ShowComponent condition={!isPending}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            padding: 2,
            borderRadius: 1,
            gap: 1.5,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Typography typography="s1" fontWeight={500}>
            Test Run Performance Metrics
          </Typography>
          <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
            <PerformanceCard
              icon={
                <Iconify
                  icon="hugeicons:checkmark-circle-04"
                  width={20}
                  height={20}
                  sx={{ color: "blue.500" }}
                />
              }
              caption="Pass Rate"
              value={`${data?.test_run_performance_metrics?.pass_rate}%`}
            />
            <PerformanceCard
              icon={
                <Iconify
                  icon="hugeicons:user-multiple-02"
                  width={20}
                  height={20}
                  sx={{ color: "primary.main" }}
                />
              }
              caption="Total Test Runs"
              value={data?.test_run_performance_metrics?.total_test_runs}
            />
            <PerformanceCard
              icon={
                <Iconify
                  icon="hugeicons:minus-sign-circle"
                  width={20}
                  height={20}
                  sx={{ color: "red.500" }}
                />
              }
              caption="Latest Fail Rate"
              value={data?.test_run_performance_metrics?.latest_fail_rate}
            />
          </Box>
          <Typography typography="s1" fontWeight={500}>
            Top Performing Scenarios
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(2, 1fr)",
              gap: "1px",
            }}
          >
            {data?.top_performing_scenarios?.map((scenario) => (
              <Box
                key={scenario.scenario_name}
                sx={{
                  backgroundColor: "background.default",
                  borderRadius: 1,
                  display: "flex",
                  paddingX: 2,
                  paddingY: 1,
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Box sx={{ display: "flex", flexDirection: "column" }}>
                  <Typography typography="s1" fontWeight={500}>
                    {scenario.scenario_name}
                  </Typography>
                  <Typography typography="s2" color="text.disabled">
                    {scenario.test_count} Tests
                  </Typography>
                </Box>
                <ShowComponent
                  condition={
                    scenario.performance_score !== null ||
                    scenario.performance_score !== undefined
                  }
                >
                  <Box>
                    <Box
                      sx={{
                        border: "1px solid",
                        borderRadius: 1,
                        paddingX: 1,
                        paddingY: 0.2,
                        backgroundColor: `${colorMap(parseFloat(scenario.performance_score))}.50`,
                        color: `${colorMap(parseFloat(scenario.performance_score))}.500`,
                        borderColor: `${colorMap(parseFloat(scenario.performance_score))}.10`,
                      }}
                    >
                      <Typography typography="s2">
                        {scenario.performance_score}
                      </Typography>
                    </Box>
                  </Box>
                </ShowComponent>
              </Box>
            ))}
          </Box>
        </Box>
      </ShowComponent>
    </Box>
  );
};

export default PerformanceDetails;
