import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import CustomTooltip from "src/components/tooltip";
import { STATUS_CHIPS } from "./traceTable.constants";

// Outcome quick-filter — one chip per status bucket plus All. The active chip is
// the selected (grey) state; the dot carries the status colour. Each chip
// carries its count so the distribution is legible before you click. Counts follow the table's other filters, not the status one.
export default function StatusFilterChips({ value, counts, onChange, blocked = [] }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75 }}>
      {STATUS_CHIPS.map((chip) => {
        const active = value === chip.id;
        const count = counts[chip.id] ?? 0;
        const blockedByGrouping = blocked.includes(chip.id);
        const disabled = (chip.id !== "all" && count === 0) || blockedByGrouping;
        return (
          <CustomTooltip
            key={chip.id}
            show={blockedByGrouping}
            arrow
            title={blockedByGrouping
              ? "Not available while grouping by a failure view. Passing runs have no failure bucket"
              : ""}
          >
            <Box
              role="button"
              aria-label={chip.label}
              aria-pressed={active}
              tabIndex={disabled ? -1 : 0}
              onClick={() => !disabled && onChange(chip.id)}
              onKeyDown={(e) => { if (!disabled && (e.key === "Enter" || e.key === " ")) onChange(chip.id); }}
              sx={{
                display: "inline-flex", alignItems: "center", gap: 0.625,
                height: 28, px: 1.125, borderRadius: 1,
                border: "1px solid",
                borderColor: active ? "text.primary" : "divider",
                bgcolor: active ? "action.selected" : "transparent",
                color: disabled ? "text.disabled" : "text.primary",
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.5 : 1,
                transition: "border-color 120ms, background-color 120ms",
                "&:hover": disabled ? {} : { borderColor: active ? "text.primary" : "text.disabled" },
              }}
            >
              {chip.id !== "all" && (
                <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: chip.role ? `status.${chip.role}.solid` : "text.disabled", flexShrink: 0 }} />
              )}
              <Typography sx={{ typography: "s2", fontWeight: 500, whiteSpace: "nowrap" }}>
                {chip.label}
              </Typography>
              <Typography sx={{
                typography: "s3", fontWeight: 500, fontVariantNumeric: "tabular-nums",
                color: active ? "inherit" : "text.subtitle",
              }}>
                {count}
              </Typography>
            </Box>
          </CustomTooltip>
        );
      })}
    </Stack>
  );
}
StatusFilterChips.propTypes = {
  value: PropTypes.string.isRequired,
  counts: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  blocked: PropTypes.arrayOf(PropTypes.string),
};
