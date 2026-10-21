import PropTypes from "prop-types";
import { Box, Stack, Typography, TextField } from "@mui/material";
import { MAX_SCENARIOS, isValidScenarioCount } from "./scenarioCountRules";

// The shared "Scenarios to generate" field, rendered in every build-source flow.
// Digits only, default 10, capped at MAX_SCENARIOS; shows an error state until a
// valid count (1..MAX) is set — the build form blocks submit while it is invalid.
export default function ScenarioCount({ value, onChange }) {
  const n = Number(value) || 0;
  const valid = isValidScenarioCount(value);

  const onInput = (raw) => {
    const digits = raw.replace(/\D/g, "").replace(/^0+/, "").slice(0, 4);
    onChange(digits && Number(digits) > MAX_SCENARIOS ? String(MAX_SCENARIOS) : digits);
  };

  return (
    <Box
      sx={{
        px: 1.75, py: 1.25, borderRadius: 1, border: "1px solid",
        borderColor: valid ? "divider" : "error.main",
      }}
    >
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
        Scenarios to generate
      </Typography>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ xs: "flex-start", sm: "center" }}
        spacing={{ xs: 0.75, sm: 1.5 }}
        sx={{ mt: 1 }}
      >
        <TextField
          size="small"
          value={value}
          onChange={(e) => onInput(e.target.value)}
          error={!valid}
          placeholder="e.g. 25"
          inputProps={{ inputMode: "numeric", "aria-label": "Number of scenarios to generate" }}
          InputProps={{
            endAdornment: n ? (
              <Typography sx={{ typography: "s3", color: "text.subtitle", ml: 0.5, whiteSpace: "nowrap" }}>
                {n === 1 ? "scenario" : "scenarios"}
              </Typography>
            ) : null,
          }}
          sx={{ width: 150, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2", fontVariantNumeric: "tabular-nums" } }}
        />
        <Typography sx={{ typography: "s3", color: valid ? "text.subtitle" : "error.main", minWidth: 0 }}>
          {valid
            ? `We'll generate ${n} scenario${n === 1 ? "" : "s"}. Set any number up to ${MAX_SCENARIOS}.`
            : `Enter a number between 1 and ${MAX_SCENARIOS}.`}
        </Typography>
      </Stack>
    </Box>
  );
}

ScenarioCount.propTypes = { value: PropTypes.string, onChange: PropTypes.func };
