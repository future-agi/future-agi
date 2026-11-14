import { Box, Chip, styled, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { commonBorder } from "src/sections/experiment-detail/ExperimentData/Common";

import TraceDataList from "../CompareDrawer/TraceDataList";

const Section = styled(Box)(() => ({
  padding: "14px",
}));

const RunDetails = ({ traceData }) => {
  const theme = useTheme();

  const systemMetrics = traceData?.systemMetrics || {};

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRight: commonBorder.border,
        borderColor: commonBorder.borderColor,
      }}
    >
      <Section
        sx={{
          gap: theme.spacing(1.75),
          borderBottom: commonBorder.border,
          borderColor: commonBorder.borderColor,
          paddingX: theme.spacing(2),
        }}
      >
        <Typography
          color="text.primary"
          lineHeight={"22px"}
          fontWeight={600}
          marginBottom={theme.spacing(1.75)}
        >
          System Metrics:
        </Typography>
        <Box sx={{ display: "flex", gap: theme.spacing(1) }}>
          <Box sx={{ display: "flex" }}>
            <Chip
              variant="outlined"
              label={
                <Typography
                  variant="s3"
                  sx={{ color: "text.primary" }}
                  fontWeight={"fontWeightRegular"}
                >
                  Total Cost: ${systemMetrics?.avgCost}
                </Typography>
              }
              icon={
                <img
                  src={`/assets/icons/components/ic_newcoin.svg`}
                  alt="Coins Icon"
                  style={{
                    width: 15,
                    height: 15,
                  }}
                />
              }
              sx={{
                backgroundColor: "divider",
                borderColor: "divider",
              }}
            />
          </Box>
          <Box sx={{ display: "flex" }}>
            <Chip
              variant="outlined"
              label={
                <Typography
                  variant="s3"
                  sx={{ color: "text.primary" }}
                  fontWeight={"fontWeightRegular"}
                >
                  Latency: {systemMetrics?.avgLatencyMs}ms
                </Typography>
              }
              icon={
                <Iconify
                  icon="solar:clock-circle-linear"
                  width={16}
                  color="text.disabled"
                />
              }
              sx={{
                backgroundColor: "divider",
                borderColor: "divider",
              }}
            />
          </Box>
        </Box>
      </Section>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          paddingX: theme.spacing(2),
          paddingTop: theme.spacing(1.75),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        }}
      >
        <Typography fontWeight={600}>Run Details</Typography>

        <Box
          sx={{
            flex: 1,
            overflowY: "auto",
            paddingBottom: "20px",
            "&::-webkit-scrollbar": {
              width: "5px !important",
              height: "5px !important",
            },
            "&::-webkit-scrollbar-thumb": {
              backgroundColor: "border.strong",
              borderRadius: "4px",
            },
            "&::-webkit-scrollbar-track": {
              backgroundColor: "transparent",
            },
          }}
        >
          <Box display="flex" width="100%">
            <TraceDataList traceData={traceData} />
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

RunDetails.propTypes = {
  traceData: PropTypes.object,
};

export default RunDetails;
