import PropTypes from "prop-types";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import { STATUS_META, LIVE_STATUSES, PULSING_DOT_CLASS } from "./runs.constants";

const pulse = keyframes`
  0%   { opacity: 1;   transform: scale(1); }
  50%  { opacity: 0.45; transform: scale(0.82); }
  100% { opacity: 1;   transform: scale(1); }
`;

const ripple = keyframes`
  0%   { transform: scale(0.7); opacity: 0.55; }
  100% { transform: scale(2.6); opacity: 0; }
`;

function StatusDot({ status, size = 6, live }) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  const animate = live ?? LIVE_STATUSES.includes(status);
  return (
    <Box
      sx={{
        position: "relative",
        display: "grid",
        placeItems: "center",
        width: size * 2,
        height: size * 2,
        flexShrink: 0,
      }}
    >
      {animate && (
        <Box
          sx={{
            position: "absolute",
            width: size,
            height: size,
            borderRadius: "50%",
            bgcolor: meta.color,
            animation: `${ripple} 1.6s ease-out infinite`,
          }}
        />
      )}
      <Box
        className={animate ? PULSING_DOT_CLASS : undefined}
        sx={{
          width: size,
          height: size,
          borderRadius: "50%",
          bgcolor: meta.color,
          animation: animate ? `${pulse} 1.6s ease-in-out infinite` : "none",
        }}
      />
    </Box>
  );
}

StatusDot.propTypes = {
  status: PropTypes.string,
  size: PropTypes.number,
  live: PropTypes.bool,
};

export default function StatusChip({ status }) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.25}
      sx={{
        pl: 0.25,
        pr: 1,
        height: 22,
        borderRadius: 0.75,
        color: meta.color,
        bgcolor: (t) => alpha(meta.color, t.palette.mode === "dark" ? 0.16 : 0.1),
        border: () => `1px solid ${alpha(meta.color, 0.24)}`,
      }}
    >
      <StatusDot status={status} size={6} />
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}>
        {meta.label}
      </Typography>
    </Stack>
  );
}

StatusChip.propTypes = { status: PropTypes.string };
