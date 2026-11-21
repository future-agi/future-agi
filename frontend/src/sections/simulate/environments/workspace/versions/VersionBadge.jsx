import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { STATUS_ROLES } from "src/utils/runStatus";

// A small label beside a version's name, e.g. "runs use this" or "draft".
export function VersionBadge({ label, tone, role }) {
  const statusRole = role || (STATUS_ROLES.includes(tone) ? tone : null);
  return (
    <Box
      sx={{
        px: 0.75,
        height: 18,
        borderRadius: "4px",
        display: "inline-flex",
        alignItems: "center",
        color: statusRole ? `status.${statusRole}.fg` : "text.secondary",
        bgcolor: statusRole ? `status.${statusRole}.bg` : "action.selected",
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 500 }}>
        {label}
      </Typography>
    </Box>
  );
}
VersionBadge.propTypes = {
  label: PropTypes.string,
  tone: PropTypes.string,
  role: PropTypes.oneOf(STATUS_ROLES),
};
