import { Box, Link, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";

import { useHostUpdateStatus } from "src/api/hostUpdates/host-update-status";
import { paths } from "src/routes/paths";
import { noticeForStatus } from "src/sections/settings/Updates/explainer-sections";

export default function HostUpdateNotice() {
  const { data, automaticNotice } = useHostUpdateStatus();
  const notice = automaticNotice || noticeForStatus(data);
  if (!notice) return null;

  return (
    <Box
      role="status"
      aria-live="polite"
      aria-atomic="true"
      sx={{
        px: 2,
        py: 1,
        bgcolor: "background.neutral",
        borderBottom: 1,
        borderColor: "divider",
        flexShrink: 0,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "baseline",
        gap: 1,
      }}
    >
      <Typography variant="body2" component="span" color="text.primary">
        {notice.text}
      </Typography>
      <Link
        component={RouterLink}
        to={paths.dashboard.settings.updates}
        variant="body2"
        color="text.primary"
        underline="always"
      >
        View update details
      </Link>
    </Box>
  );
}
