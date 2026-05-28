import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { ObservePanelActions, ObservePanelHeader } from "./observe-panel-utils";
import { readableToken } from "../onboarding-home.constants";

const DAILY_QUALITY_HREF = `${paths.dashboard.home}?mode=daily-quality`;

export default function FirstLoopCompletePanel({
  action,
  fallbackAction,
  lastMeaningfulEvent,
  primaryPath,
  onPrimaryClick,
  onFallbackClick,
  onCheckAgain,
  isChecking = false,
}) {
  const productPath = primaryPath || action?.analytics?.targetPath;
  const pathLabel = productPath ? readableToken(productPath) : null;

  return (
    <Box
      data-testid="first-loop-complete-panel"
      sx={{
        border: "1px solid",
        borderColor: "success.main",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={2}>
        <ObservePanelHeader
          eyebrow="Aha moment reached"
          title="Your first quality loop is live"
          description="A product signal is now connected to a repeatable quality check. Keep this loop warm before adding more setup."
          chips={[pathLabel, "complete"].filter(Boolean)}
        />
        <Stack spacing={0.5}>
          <Typography variant="subtitle2">Next best step</Typography>
          <Typography variant="body2" color="text.secondary">
            Review daily quality next, then open the current loop when a signal
            needs attention.
          </Typography>
        </Stack>
        {lastMeaningfulEvent ? (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1.5,
            }}
          >
            <Typography variant="subtitle2">Latest proof</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {lastMeaningfulEvent.name}
            </Typography>
          </Box>
        ) : null}
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button
            variant="contained"
            component={RouterLink}
            href={DAILY_QUALITY_HREF}
            startIcon={<Iconify icon="mdi:calendar-check" width={18} />}
          >
            Review daily quality
          </Button>
          <ObservePanelActions
            action={action}
            fallbackAction={fallbackAction}
            onPrimaryClick={onPrimaryClick}
            onFallbackClick={onFallbackClick}
            onCheckAgain={onCheckAgain}
            isChecking={isChecking}
            primaryVariant="outlined"
          />
        </Stack>
      </Stack>
    </Box>
  );
}

FirstLoopCompletePanel.propTypes = {
  action: PropTypes.object,
  fallbackAction: PropTypes.object,
  isChecking: PropTypes.bool,
  lastMeaningfulEvent: PropTypes.object,
  onCheckAgain: PropTypes.func,
  onFallbackClick: PropTypes.func,
  onPrimaryClick: PropTypes.func,
  primaryPath: PropTypes.string,
};
