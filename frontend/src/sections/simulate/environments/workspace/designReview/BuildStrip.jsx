import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import BuildPhaseTable from "../BuildPhaseTable";
import {
  BUILD_STEP_TOTAL,
  phaseOfStep,
  phaseRows,
  stepOfStage,
} from "../buildSteps";
import { BUILD_COPY, elapsedWords } from "./designReview.constants";

function Spinner() {
  return (
    <Box
      sx={{
        width: 10,
        height: 10,
        borderRadius: "50%",
        flexShrink: 0,
        border: "1.5px solid",
        borderColor: "primary.main",
        borderTopColor: "transparent",
        animation: "build-strip-spin 0.7s linear infinite",
        "@keyframes build-strip-spin": { to: { transform: "rotate(360deg)" } },
      }}
    />
  );
}

export default function BuildStrip({ name, stage, startedAt, readsAgent }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const step = stepOfStage(stage);
  const phase = phaseOfStep(step);
  const started = startedAt ? Date.parse(startedAt) : NaN;
  return (
    <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
      <Box
        role="status"
        aria-live="polite"
        data-testid="build-strip"
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          overflow: "hidden",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2, py: 1.25 }}
        >
          <Spinner />
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              flex: 1,
              minWidth: 0,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {BUILD_COPY.line(name, phase?.label, step?.label)}
            {step && (
              <Box
                component="span"
                sx={{ color: "text.tertiary", fontWeight: 500 }}
              >
                {BUILD_COPY.count(step.no, BUILD_STEP_TOTAL)}
              </Box>
            )}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
          >
            {Number.isFinite(started)
              ? BUILD_COPY.after(elapsedWords(now - started))
              : BUILD_COPY.afterNoClock}
          </Typography>
        </Stack>
        {step && <BuildPhaseTable rows={phaseRows(step, { readsAgent })} />}
      </Box>
    </Box>
  );
}

BuildStrip.propTypes = {
  name: PropTypes.string,
  stage: PropTypes.string,
  startedAt: PropTypes.string,
  readsAgent: PropTypes.bool,
};
