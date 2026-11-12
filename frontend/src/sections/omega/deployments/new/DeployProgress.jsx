import React, { useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import DeployIcon from "../components/DeployIcon";
import { HOST_ONLY_STATUS } from "../lib/reach";

export const PROGRESS_STEPS = ["queued", "building", "deploying", "live"];
const LABELS = {
  queued: "Queued",
  building: "Building",
  deploying: "Deploying",
  live: "Live",
};

/**
 * Queued -> Building -> Deploying -> Live, from the statuses EE reports for
 * one operation. Polling can miss a short stage, so the line remembers the
 * furthest stage it saw; a failure marks the stage it was in (building when
 * nothing later was seen). A deployment only this host can reach ends on
 * "Running on this host", never "Live" (`hostOnly`, lib/reach.js; DEMO-FIX-2
 * item 1).
 */
export function useFurthestStage(status) {
  const furthest = useRef(-1);
  const index = PROGRESS_STEPS.indexOf(status);
  if (index > furthest.current) furthest.current = index;
  return furthest.current;
}

export default function DeployProgress({ status, message, hostOnly = false }) {
  const furthest = useFurthestStage(status);
  const labels = hostOnly ? { ...LABELS, live: HOST_ONLY_STATUS } : LABELS;
  const failed = status === "failed";
  // A revision fails while building or deploying, never while queued.
  const failedAt = failed ? Math.max(furthest, 1) : -1;
  return (
    <Box data-testid="deploy-progress" data-status={status}>
      <Box
        component="ol"
        sx={{
          listStyle: "none",
          m: 0,
          p: 0,
          display: "grid",
          gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, 1fr)" },
          gap: 1,
        }}
      >
        {PROGRESS_STEPS.map((step, index) => {
          const done = !failed
            ? index <= furthest && (index < furthest || step === "live")
            : index < failedAt;
          const current = !failed && index === furthest && step !== "live";
          const broke = failed && index === failedAt;
          return (
            <Box
              component="li"
              key={step}
              data-testid={`progress-${step}`}
              data-state={
                broke
                  ? "failed"
                  : done
                    ? "done"
                    : current
                      ? "current"
                      : "pending"
              }
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                p: 1,
                borderRadius: 1,
                border: "1px solid",
                borderColor: current
                  ? "primary.main"
                  : broke
                    ? "error.main"
                    : "divider",
                bgcolor: "background.paper",
              }}
            >
              <Box
                sx={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  color: done
                    ? "common.white"
                    : broke
                      ? "error.main"
                      : current
                        ? "primary.main"
                        : "text.disabled",
                  bgcolor: done ? "success.main" : "transparent",
                  border: done ? "none" : "1.5px solid",
                  borderColor: broke
                    ? "error.main"
                    : current
                      ? "primary.main"
                      : "divider",
                  typography: "s3",
                  fontWeight: 600,
                }}
              >
                {done ? (
                  <DeployIcon
                    name="check"
                    size={13}
                    sx={{ strokeWidth: 2.5 }}
                  />
                ) : broke ? (
                  "!"
                ) : (
                  index + 1
                )}
              </Box>
              <Typography
                typography="s1"
                fontWeight={
                  current || broke ? "fontWeightMedium" : "fontWeightRegular"
                }
                color={
                  done || current
                    ? "text.primary"
                    : broke
                      ? "error.main"
                      : "text.secondary"
                }
              >
                {broke ? `${labels[step]} failed` : labels[step]}
              </Typography>
            </Box>
          );
        })}
      </Box>
      {message && (
        <Typography
          typography="s2"
          color={failed ? "error.main" : "text.secondary"}
          sx={{ mt: 1, overflowWrap: "anywhere" }}
          data-testid="deploy-progress-message"
        >
          {message}
        </Typography>
      )}
    </Box>
  );
}

DeployProgress.propTypes = {
  status: PropTypes.string,
  message: PropTypes.string,
  hostOnly: PropTypes.bool,
};
