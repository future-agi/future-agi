import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

/*
  The run's spending limit while it plays. Two moments get a line under the
  header: the warning threshold (Continue · Lower limit · Stop), and the limit
  reached or a stop asked for — no new scenarios start, the running ones
  finish, and what they may still use is said, not hidden.
*/
export default function SpendCard({
  spend,
  stats,
  voice,
  onContinue,
  onLowerLimit,
  onStop,
  providerName,
}) {
  const [lowering, setLowering] = useState(false);
  const [value, setValue] = useState("");
  if (!spend?.cap) return null;
  const unit = voice ? "call" : "scenario";
  const fmt = (n) => Math.round(n).toLocaleString();
  const inUse = spend.used + spend.running;

  let tone = null;
  let title = "";
  let line = "";
  if (spend.reached) {
    tone = "warning";
    title =
      spend.reached === "stopped"
        ? `Cancelling — no new ${unit}s start`
        : spend.reached === "credits"
          ? `Low on credits this month — no new ${unit}s start`
          : spend.reached === "provider"
            ? `Your ${providerName || "voice platform"} account hit its limit — no new ${unit}s start`
            : `Limit reached — no new ${unit}s start`;
    line = [
      `${fmt(spend.used)} of ${fmt(spend.cap)} credits used`,
      stats.active > 0
        ? `${stats.active} ${unit}${stats.active === 1 ? "" : "s"} still running may use up to ~${fmt(spend.running)} more`
        : null,
      stats.notStarted > 0 ? `${stats.notStarted} won't start` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  } else if (spend.alert) {
    tone = "warning";
    title = `${Math.round((inUse / spend.cap) * 100)}% of this run's limit`;
    line = `${fmt(spend.used)} used and ~${fmt(spend.running)} held by running ${unit}s, of ${fmt(spend.cap)} credits.`;
  }
  if (!tone) return null;

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      role="status"
      sx={{
        px: 2,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "status.warning.bg",
        flexWrap: "wrap",
      }}
    >
      <Iconify
        icon="solar:wallet-money-linear"
        width={18}
        sx={{ color: "status.warning.fg", flexShrink: 0 }}
      />
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography
          sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
        >
          {title}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {line}
        </Typography>
      </Box>
      {!spend.reached && !lowering && (
        <Stack direction="row" gap={1} flexShrink={0}>
          <Button
            size="small"
            onClick={onContinue}
            sx={{ color: "text.secondary" }}
          >
            Continue
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              setValue(String(Math.ceil(inUse / 50) * 50));
              setLowering(true);
            }}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Lower limit
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={onStop}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            {`Stop new ${unit}s`}
          </Button>
        </Stack>
      )}
      {!spend.reached && lowering && (
        <Stack direction="row" gap={1} alignItems="center" flexShrink={0}>
          <TextField
            size="small"
            type="number"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">credits</InputAdornment>
              ),
            }}
            inputProps={{
              min: 1,
              "aria-label": "New run limit in credits",
              style: { textAlign: "right", fontVariantNumeric: "tabular-nums" },
            }}
            sx={{ width: 160 }}
          />
          <Button
            size="small"
            onClick={() => setLowering(false)}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            disabled={!(Number(value) > 0) || Number(value) >= spend.cap}
            onClick={() => {
              setLowering(false);
              onLowerLimit(Number(value));
            }}
          >
            Apply
          </Button>
        </Stack>
      )}
    </Stack>
  );
}

SpendCard.propTypes = {
  spend: PropTypes.object,
  stats: PropTypes.object,
  voice: PropTypes.bool,
  onContinue: PropTypes.func,
  onLowerLimit: PropTypes.func,
  onStop: PropTypes.func,
  /* The voice platform whose own limit stopped new calls. */
  providerName: PropTypes.string,
};
