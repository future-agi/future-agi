import PropTypes from "prop-types";
import { Box, Stack, Typography, TextField } from "@mui/material";

// The shared "Scenarios to generate" field, rendered in every build-source flow.
// Digits only, defaults to 10. No hard cap or blocking validation here — the
// admission ceiling is deployment-settable on the backend, so it stays the
// authority: an out-of-range value surfaces as its error rather than being
// second-guessed in the UI, and a blank falls back to the backend default.
export default function ScenarioCount({ value, onChange }) {
  const n = Number(value) || 0;
  const onInput = (raw) => onChange(raw.replace(/\D/g, "").replace(/^0+/, "").slice(0, 4));

  return (
    <Box sx={{ px: 1.75, py: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider" }}>
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
        <Typography sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}>
          {n
            ? `We'll generate ${n} scenario${n === 1 ? "" : "s"} for your agent.`
            : "Leave blank to use the default."}
        </Typography>
      </Stack>
    </Box>
  );
}

ScenarioCount.propTypes = { value: PropTypes.string, onChange: PropTypes.func };
