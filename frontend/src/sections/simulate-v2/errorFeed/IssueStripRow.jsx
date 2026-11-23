import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/* One row under the header: a status badge where there is a state, the
   sentence, a quiet second line, and at most one secondary button. */
export default function IssueStripRow({ badge, icon, children, sub, action }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        bgcolor: "background.paper",
      }}
    >
      {badge && <Box sx={{ flexShrink: 0 }}>{badge}</Box>}
      {icon && (
        <Iconify
          icon={icon}
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      )}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          {children}
        </Typography>
        {sub && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {sub}
          </Typography>
        )}
      </Box>
      {action}
    </Stack>
  );
}
IssueStripRow.propTypes = {
  badge: PropTypes.node,
  icon: PropTypes.string,
  children: PropTypes.node,
  sub: PropTypes.node,
  action: PropTypes.node,
};

