import PropTypes from "prop-types";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StoppedNote from "./StoppedNote";
import TabSkeleton from "./TabSkeletons";
import { TAB_WAITING_COPY } from "./workspace.constants";

export default function TabWaiting({ tabId, tabLabel, wait }) {
  if (wait.stopped)
    return (
      <Box sx={{ px: 3, py: 2.5 }} data-testid="tab-waiting">
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              px: 1.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {tabLabel}
          </Typography>
          <StoppedNote wait={wait} />
        </Box>
      </Box>
    );
  const text = wait.now
    ? TAB_WAITING_COPY.now(wait.phase, tabLabel)
    : TAB_WAITING_COPY.next(tabLabel, wait.phase);
  return (
    <Box sx={{ px: 3, py: 2.5 }} data-testid="tab-waiting">
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        {wait.now ? (
          <CircularProgress size={12} thickness={5} sx={{ flexShrink: 0 }} />
        ) : (
          <Iconify
            icon="solar:clock-circle-linear"
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
        )}
        <Typography
          sx={{
            typography: "s2",
            fontWeight: wait.now ? 600 : 500,
            color: wait.now ? "text.primary" : "text.secondary",
          }}
        >
          {text}
        </Typography>
      </Stack>
      <TabSkeleton tabId={tabId} />
    </Box>
  );
}

TabWaiting.propTypes = {
  tabId: PropTypes.string.isRequired,
  tabLabel: PropTypes.string.isRequired,
  wait: PropTypes.shape({
    phase: PropTypes.string.isRequired,
    now: PropTypes.bool,
    stopped: PropTypes.bool,
    step: PropTypes.string,
    reason: PropTypes.string,
  }).isRequired,
};
