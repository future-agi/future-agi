import { Box, styled, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { commonBorder } from "src/sections/experiment-detail/ExperimentData/Common";

import RunTraceTree from "../CompareDrawer/RunTraceTree";

const Section = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1),
}));

const RunTrace = ({ traceData }) => {
  const observationSpans = useMemo(() => {
    if (!traceData?.observationSpans) {
      return;
    }
    return traceData?.observationSpans;
  }, [traceData]);

  const theme = useTheme();

  return (
    <Box
      sx={{
        paddingY: theme.spacing(1),
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        borderRight: commonBorder.border,
        borderColor: commonBorder.borderColor,
      }}
    >
      <Section
        sx={{
          flexShrink: 0,
          gap: theme.spacing(1.75),
          borderBottom: commonBorder.border,
          borderColor: commonBorder.borderColor,
          paddingX: theme.spacing(2),
        }}
      >
        <Typography fontWeight={600}>
          {traceData?.projectVersionName}
        </Typography>
      </Section>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          mb: "110px",
        }}
      >
        <RunTraceTree observationSpans={observationSpans} />
      </Box>
    </Box>
  );
};

RunTrace.propTypes = {
  traceData: PropTypes.object,
};

export default RunTrace;
