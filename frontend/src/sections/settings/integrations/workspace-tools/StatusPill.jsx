import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

/* The connection status pill and the PR state chip, shared by the three pages. */
export const ON_GREEN = (t) =>
  t.palette.mode === "dark" ? "#4ADE80" : "#15803D";

export function StatusPill({
  on,
  size = "medium",
  labels = ["Connected", "Not connected"],
}) {
  const small = size === "small";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: small ? 0.75 : 1,
        height: small ? 20 : 24,
        borderRadius: 0.75,
        flexShrink: 0,
        bgcolor: (t) =>
          on
            ? alpha(ON_GREEN(t), t.palette.mode === "dark" ? 0.12 : 0.1)
            : alpha(t.palette.text.primary, 0.06),
      }}
    >
      <Box
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: (t) => (on ? ON_GREEN(t) : t.palette.text.disabled),
        }}
      />
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: (t) => (on ? ON_GREEN(t) : t.palette.text.secondary),
        }}
      >
        {on ? labels[0] : labels[1]}
      </Typography>
    </Stack>
  );
}
StatusPill.propTypes = {
  on: PropTypes.bool,
  size: PropTypes.oneOf(["small", "medium"]),
  labels: PropTypes.arrayOf(PropTypes.string),
};

export function PrStatus({ status }) {
  const meta = {
    open: { icon: "solar:code-square-linear", color: "#16A34A", label: "Open" },
    merged: {
      icon: "solar:branching-paths-up-linear",
      color: "#8250DF",
      label: "Merged",
    },
    closed: {
      icon: "solar:close-circle-linear",
      color: "#DC2626",
      label: "Closed",
    },
  }[status] || {
    icon: "solar:clock-circle-linear",
    color: "#CA8A04",
    text: "text.secondary",
    label: "Pending",
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.75,
        height: 22,
        borderRadius: 0.75,
        flexShrink: 0,
        color: meta.color,
        bgcolor: alpha(meta.color, 0.12),
      }}
    >
      <Iconify icon={meta.icon} width={13} />
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 700,
          color: meta.text || "inherit",
        }}
      >
        {meta.label}
      </Typography>
    </Stack>
  );
}
PrStatus.propTypes = { status: PropTypes.string };
