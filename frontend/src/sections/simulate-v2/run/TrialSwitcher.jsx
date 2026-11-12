import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import { VERDICT_TONE } from "../components/primitives";

/*
  Which trial the drawer shows (glossary: a trial is "one attempt at one
  scenario within a run"). One button per trial, each with its own verdict
  dot, so a flaky scenario's passing and failing attempts are both a click
  away. Shown only when the scenario ran more than once.
*/
const toneOf = (s) => VERDICT_TONE[s] || VERDICT_TONE.unmeasured;

export default function TrialSwitcher({ samples, index, onPick }) {
  if (!Array.isArray(samples) || samples.length < 2) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ flexShrink: 0 }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>Trial</Typography>
      {samples.map((s, i) => (
        <Tooltip key={i} arrow title={`Trial ${i + 1} · ${toneOf(s).label}`}>
          <ButtonBase
            onClick={() => onPick(i)}
            aria-pressed={i === index}
            aria-label={`Trial ${i + 1}, ${toneOf(s).label}`}
            sx={{
              gap: 0.5,
              px: 0.75,
              height: 22,
              borderRadius: "4px",
              border: "1px solid",
              borderColor: i === index ? "text.primary" : "divider",
              bgcolor: i === index ? "background.neutral" : "transparent",
              typography: "s2",
              fontWeight: i === index ? 600 : 400,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            <Box
              sx={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                bgcolor: toneOf(s).color,
              }}
            />
            {i + 1}
          </ButtonBase>
        </Tooltip>
      ))}
    </Stack>
  );
}
TrialSwitcher.propTypes = {
  samples: PropTypes.arrayOf(PropTypes.string),
  index: PropTypes.number,
  onPick: PropTypes.func.isRequired,
};
