import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Grid } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { PREFLIGHT_STATE_COLORS, RUNS_COPY } from "./runs.constants";

// One cell of the pre-flight grid: an icon tile, a label, the current value and
// a status line. When the slot is not satisfied and a fixer is supplied, a small
// "Fix" button jumps to the tab that resolves it.
export default function PreflightItem({ xs, md, label, value, sub, icon, color, ok, warn, onFix }) {
  const state = ok
    ? warn
      ? PREFLIGHT_STATE_COLORS.warn
      : PREFLIGHT_STATE_COLORS.ok
    : PREFLIGHT_STATE_COLORS.blocked;
  const tone = color || BUILD_TONES.accent;
  const stateIcon = ok
    ? warn
      ? "solar:info-circle-bold"
      : "solar:check-circle-bold"
    : "solar:close-circle-bold";
  return (
    <Grid item xs={xs} md={md}>
      <Stack direction="row" spacing={1.25} alignItems="flex-start" sx={{ pr: 2 }}>
        <Box
          sx={{
            width: 32,
            height: 32,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.16 : 0.1),
            color: tone,
          }}
        >
          <Iconify icon={icon} width={16} />
        </Box>
        <Box minWidth={0}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
          <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
            {value}
          </Typography>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Iconify icon={stateIcon} width={11} sx={{ color: state, flexShrink: 0 }} />
            <Typography noWrap sx={{ typography: "s3", color: state }}>
              {sub}
            </Typography>
            {!ok && onFix && (
              <Button
                size="small"
                onClick={onFix}
                sx={{ minWidth: 0, px: 0.5, typography: "s3", fontWeight: "fontWeightBold" }}
              >
                {RUNS_COPY.fix}
              </Button>
            )}
          </Stack>
        </Box>
      </Stack>
    </Grid>
  );
}

PreflightItem.propTypes = {
  xs: PropTypes.number,
  md: PropTypes.number,
  label: PropTypes.string,
  value: PropTypes.node,
  sub: PropTypes.node,
  icon: PropTypes.string,
  color: PropTypes.string,
  ok: PropTypes.bool,
  warn: PropTypes.bool,
  onFix: PropTypes.func,
};
