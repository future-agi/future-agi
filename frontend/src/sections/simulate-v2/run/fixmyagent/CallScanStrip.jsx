import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

/**
 * How far the debugger has read: a count, one thin bar, and the call it is on.
 * The issues themselves arrive as cards underneath, so the loader only has to
 * say how much is left.
 */

export default function CallScanStrip({ tasks, scanned }) {
  const total = tasks.length;
  const read = Math.min(scanned, total);
  const done = read >= total;
  const reading = tasks[Math.min(scanned, total - 1)];

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box
          sx={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            flexShrink: 0,
            border: "1.5px solid",
            borderColor: "primary.main",
            borderTopColor: "transparent",
            animation: "scan-spin 0.7s linear infinite",
            "@keyframes scan-spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          {done ? "Ranking the issues" : "Reading calls"}
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {read}
          <Box
            component="span"
            sx={{ color: "text.tertiary", fontWeight: 500 }}
          >
            {" "}
            / {total} analyzed
          </Box>
        </Typography>
      </Stack>

      <Box
        sx={{
          mt: 1,
          height: 4,
          borderRadius: 2,
          overflow: "hidden",
          bgcolor: (t) => alpha(t.palette.text.primary, 0.08),
        }}
      >
        <Box
          sx={{
            height: "100%",
            borderRadius: 2,
            width: `${total ? (read / total) * 100 : 0}%`,
            transition: "width 240ms ease",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.6),
          }}
        />
      </Box>

      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
      >
        {done
          ? "Every call read"
          : `Reading · ${reading?.title || reading?.id || ""}`}
      </Typography>
    </Box>
  );
}

CallScanStrip.propTypes = {
  tasks: PropTypes.array.isRequired,
  scanned: PropTypes.number.isRequired,
};
