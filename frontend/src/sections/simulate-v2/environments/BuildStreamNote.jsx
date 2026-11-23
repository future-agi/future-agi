import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  One line over a tab while the build writes it: what it's doing and how far
  along, or — before its turn — what it's waiting for.
*/
export default function BuildStreamNote({
  label,
  count = null,
  total = null,
  waiting = false,
  stopped = false,
  sx,
}) {
  /* A build that stopped: nothing is moving, so no spinner. */
  const quiet = waiting || stopped;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 0.5, mb: 2, ...sx }}
    >
      {quiet ? (
        <Iconify
          icon={
            stopped ? "solar:stop-circle-linear" : "solar:clock-circle-linear"
          }
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      ) : (
        <Box
          sx={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            flexShrink: 0,
            border: "1.5px solid",
            borderColor: "status.discovery.solid",
            borderTopColor: "transparent",
            animation: "bsn-spin 0.7s linear infinite",
            "@keyframes bsn-spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
      )}
      <Typography
        sx={{
          typography: "s2",
          fontWeight: quiet ? 500 : 600,
          color: quiet ? "text.subtitle" : "text.primary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {label}
        {count != null && total != null && (
          <Box
            component="span"
            sx={{ color: "text.subtitle", fontWeight: 500 }}
          >{` · ${Math.min(count, total)} of ${total}`}</Box>
        )}
      </Typography>
    </Stack>
  );
}
BuildStreamNote.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
  total: PropTypes.number,
  waiting: PropTypes.bool,
  stopped: PropTypes.bool,
  sx: PropTypes.object,
};
