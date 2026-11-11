import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { VERDICT_TONE } from "../components/primitives";

/*
  Every trial of one scenario, side by side: one dot per trial in the order
  they ran, then how many passed. A flaky scenario reads as a mix of dots.
  Trial 1 is the call the row opens; the others are its repeats.
*/
const toneOf = (s) => VERDICT_TONE[s] || VERDICT_TONE.unmeasured;

export default function TrialDots({ samples }) {
  if (!Array.isArray(samples) || samples.length < 2) return null;
  const passed = samples.filter((s) => s === "passed").length;
  return (
    <Tooltip
      arrow
      title={
        <Box sx={{ whiteSpace: "pre-line" }}>
          {samples
            .map(
              (s, i) =>
                `Trial ${i + 1}${i === 0 ? " (this call)" : ""} · ${toneOf(s).label}`,
            )
            .join("\n")}
        </Box>
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ flexShrink: 0 }}
      >
        <Stack direction="row" spacing={0.375} aria-hidden>
          {samples.map((s, i) => (
            <Box
              key={i}
              sx={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                bgcolor: toneOf(s).color,
              }}
            />
          ))}
        </Stack>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${passed} of ${samples.length} trials passed`}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
TrialDots.propTypes = { samples: PropTypes.arrayOf(PropTypes.string) };
