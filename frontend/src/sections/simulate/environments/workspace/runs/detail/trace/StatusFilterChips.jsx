import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import CustomTooltip from "src/components/tooltip";
import { STATUS_CHIPS } from "./traceTable.constants";

// Outcome quick-filter: one joined group of segments, All plus one per status
// bucket. The label carries the status colour, the count stays grey, and the
// selected segment has a grey fill. Counts follow the table's other filters,
// not the status one.
// Once counts load, a bucket with no calls has no segment. All and the
// selected segment always stay, so the current filter is never lost. Before
// the first counts arrive every segment shows greyed, so the bar doesn't jump
// on first paint. A reload after that (picking a status, paging) arrives with
// the previous counts still in `counts`, so the bar stays as it was.
export default function StatusFilterChips({
  value,
  counts,
  onChange,
  blocked = [],
  loading = false,
}) {
  // RunTraceTable always adds an All total, so no calls yet means all is 0.
  const firstLoad = loading && !counts.all;

  return (
    <Stack
      direction="row"
      alignItems="stretch"
      role="group"
      aria-label="Filter by status"
      sx={{
        display: "inline-flex",
        alignSelf: "flex-start",
        flexWrap: "nowrap",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      {STATUS_CHIPS.filter(
        (chip) =>
          firstLoad ||
          chip.id === "all" ||
          chip.id === value ||
          (counts[chip.id] ?? 0) > 0,
      ).map((chip) => {
        const active = value === chip.id;
        const count = counts[chip.id] ?? 0;
        const blockedByGrouping = blocked.includes(chip.id);
        const disabled =
          (chip.id !== "all" && count === 0) || blockedByGrouping;
        return (
          <CustomTooltip
            key={chip.id}
            show={blockedByGrouping}
            arrow
            title={
              blockedByGrouping
                ? "Not available while grouping by a failure view. Passing runs have no failure bucket"
                : ""
            }
          >
            <Box
              role="button"
              aria-label={chip.label}
              aria-pressed={active}
              tabIndex={disabled ? -1 : 0}
              onClick={() => !disabled && onChange(chip.id)}
              onKeyDown={(e) => {
                if (!disabled && (e.key === "Enter" || e.key === " "))
                  onChange(chip.id);
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                // 26 + the group's 1px border = 28, the Columns button's height.
                height: 26,
                px: 1.25,
                borderLeft: "1px solid",
                borderColor: "divider",
                "&:first-of-type": { borderLeft: "none" },
                bgcolor: active ? "action.selected" : "transparent",
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.5 : 1,
                transition: "background-color 120ms",
                "&:hover":
                  disabled || active ? {} : { bgcolor: "action.hover" },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "border.focus",
                  outlineOffset: -2,
                },
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 500,
                  whiteSpace: "nowrap",
                  color: disabled
                    ? "text.disabled"
                    : chip.role
                      ? `status.${chip.role}.fg`
                      : "text.primary",
                }}
              >
                {chip.label}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 500,
                  fontVariantNumeric: "tabular-nums",
                  color: "text.secondary",
                }}
              >
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
  loading: PropTypes.bool,
};
