import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { Label } from "./ContractPart";
import { CONTRACT_COPY, MONO_FONT } from "./contract.constants";

// Part 2 — the observation and action spaces. A tinted 1px grid gap shows
// through as a divider between the two columns.
export default function ContractSpaces({ obs, acts, adapter }) {
  return (
    <Box sx={{
      display: "grid", gap: "1px",
      gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
      bgcolor: "divider",
    }}>
      <Box sx={{ p: 1.5, bgcolor: "background.paper" }}>
        <Label>{CONTRACT_COPY.observationLabel}</Label>
        <Stack spacing={1}>
          {obs.map((o) => (
            <Stack key={o.field} direction="row" spacing={1.25} alignItems="flex-start">
              <Typography sx={{ typography: "s1", fontFamily: MONO_FONT, fontWeight: 500, width: 108, flexShrink: 0 }}>
                {o.field}
              </Typography>
              <Box minWidth={0}>
                <Stack direction="row" spacing={0.75} alignItems="center">
                  <Typography sx={{ typography: "s3", fontFamily: MONO_FONT, color: "text.subtitle" }}>
                    {o.type}
                  </Typography>
                  <Box
                    component="span"
                    sx={{
                      px: 0.75, height: 18, display: "inline-flex", alignItems: "center",
                      borderRadius: "4px", typography: "s3", fontWeight: 500,
                      color: "text.secondary",
                      bgcolor: "action.selected",
                    }}
                  >
                    {o.generic ? CONTRACT_COPY.coreChip : adapter.label.toLowerCase()}
                  </Box>
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{o.filled}</Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </Box>

      <Box sx={{ p: 1.5, bgcolor: "background.paper" }}>
        <Label>{CONTRACT_COPY.actionLabel}</Label>
        <Stack spacing={1}>
          {acts.map((a) => (
            <Stack key={a.verb} direction="row" spacing={1.25} alignItems="flex-start">
              <Typography sx={{ typography: "s1", fontFamily: MONO_FONT, fontWeight: 500, width: 90, flexShrink: 0 }}>
                {a.verb}
              </Typography>
              <Box minWidth={0}>
                <Typography sx={{ typography: "s3", fontFamily: MONO_FONT, color: "text.subtitle" }}>
                  {a.args}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{a.note}</Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </Box>
    </Box>
  );
}

ContractSpaces.propTypes = {
  obs: PropTypes.arrayOf(PropTypes.shape({
    field: PropTypes.string,
    type: PropTypes.string,
    filled: PropTypes.string,
    generic: PropTypes.bool,
  })),
  acts: PropTypes.arrayOf(PropTypes.shape({
    verb: PropTypes.string,
    args: PropTypes.string,
    note: PropTypes.string,
  })),
  adapter: PropTypes.shape({
    label: PropTypes.string,
    color: PropTypes.string,
  }).isRequired,
};
