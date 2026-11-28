import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import Iconify from "src/components/iconify";

// A quiet note of the run plan: a small icon and grey text, no box. Its actions sit on the right.
export default function RunPlanNote({
  severity = "info",
  action = null,
  children,
  ...other
}) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="flex-start" {...other}>
      <Iconify
        icon={
          severity === "warning"
            ? "solar:danger-triangle-linear"
            : "solar:info-circle-linear"
        }
        width={14}
        sx={{
          mt: "1px",
          flexShrink: 0,
          color: severity === "warning" ? "status.warning.fg" : "text.tertiary",
        }}
      />
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          typography: "s3",
          color: "text.secondary",
        }}
      >
        {children}
      </Box>
      {action && (
        <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0, my: -0.5 }}>
          {action}
        </Stack>
      )}
    </Stack>
  );
}

RunPlanNote.propTypes = {
  severity: PropTypes.oneOf(["info", "warning"]),
  action: PropTypes.node,
  children: PropTypes.node,
};
