import React, { useState } from "react";
import { StyledBox } from "src/sections/projects/SessionsView/ReplaySessions/CreateScenariosForm";
import {
  Stack,
  Typography,
  IconButton,
  Collapse,
  Box,
  Grid,
} from "@mui/material";
import SvgColor from "../../../../components/svg-color/svg-color";
import PropTypes from "prop-types";
import { PerformanceMetricsSkeleton } from "./Skeletons";
import {
  getChangeText,
  formatIfFloat,
  getPerformanceMetricsLabel,
} from "./common";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { ShowComponent } from "src/components/show";

const MetricCard = ({ title, value, change, changePercent, changeText }) => {
  // Determine if the change is positive or negative
  const isPositive = changePercent > 0;
  const isNegative = changePercent < 0;

  // Get the appropriate color
  const changeColor = isPositive
    ? "status.success.fg"
    : isNegative
      ? "status.danger.fg"
      : "text.disabled";

  const changeBgColor = isPositive
    ? "status.success.bg"
    : isNegative
      ? "status.danger.bg"
      : "action.hover";

  // Get the change type text
  const changeTypeText = isPositive ? "increase" : isNegative ? "decrease" : "";
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: "background.paper",
      }}
    >
      <Stack direction={"column"} gap={0} sx={{ padding: 1.5 }}>
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          {title}
        </Typography>

        <Typography
          typography={"m1"}
          fontWeight={"fontWeightSemiBold"}
          color={"text.primary"}
        >
          {value}
        </Typography>

        <ShowComponent
          condition={
            change !== null &&
            changePercent !== null &&
            changePercent !== undefined
          }
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <Typography
              typography={"s3"}
              fontWeight={"fontWeightMedium"}
              color={changeColor}
              sx={{
                bgcolor: changeBgColor,
                padding: (theme) => theme.spacing(0.25, 1),
                borderRadius: "4px",
              }}
            >
              {change !== null && `${change > 0 ? "+" : ""}${change}`}
              {changePercent !== null &&
                changePercent !== undefined &&
                ` (${changePercent > 0 ? "+" : ""}${changePercent}%)`}
            </Typography>

            <Typography
              typography={"s3"}
              fontWeight={"fontWeightRegular"}
              color={"text.primary"}
            >
              {changeTypeText && `${changeTypeText} `}
              {changeText}
            </Typography>
          </Box>
        </ShowComponent>
      </Stack>
    </Box>
  );
};

MetricCard.propTypes = {
  title: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  change: PropTypes.number.isRequired,
  changePercent: PropTypes.number.isRequired,
  changeText: PropTypes.string.isRequired,
};

export default function PerformanceMetrics({
  data,
  isLoading,
  simulationCallType,
}) {
  const isVoice = simulationCallType === AGENT_TYPES.VOICE;
  const [collapsed, setCollapsed] = useState(false);

  if (isLoading) {
    return <PerformanceMetricsSkeleton />;
  }
  return (
    <StyledBox
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Performance Overview
        </Typography>
        <IconButton
          onClick={() => setCollapsed(!collapsed)}
          sx={{
            color: "text.primary",
            p: 0,
          }}
          size="small"
        >
          <SvgColor
            sx={{
              height: "24px",
              width: "24px",
              transform: `${!collapsed ? "rotate(180deg)" : "rotate(0deg)"}`,
              transition: "transform 0.2s ease",
              transformOrigin: "center",
            }}
            src="/assets/icons/custom/lucide--chevron-down.svg"
          />
        </IconButton>
      </Stack>
      <Collapse in={!collapsed}>
        <Grid container spacing={1}>
          {data?.map((metric) => (
            <Grid
              item
              xs={12}
              sm={6}
              md={3}
              key={metric.id}
              sx={{
                "& > div": {
                  minHeight: "102px",
                },
              }}
            >
              <MetricCard
                title={getPerformanceMetricsLabel(metric?.metric, isVoice)}
                value={formatIfFloat(metric?.value)}
                change={formatIfFloat(metric?.change)}
                changePercent={formatIfFloat(metric?.percentageChange)}
                changeText={getChangeText(isVoice)}
              />
            </Grid>
          ))}
        </Grid>
      </Collapse>
    </StyledBox>
  );
}

PerformanceMetrics.propTypes = {
  data: PropTypes.array.isRequired,
  isLoading: PropTypes.bool.isRequired,
  simulationCallType: PropTypes.string,
};
