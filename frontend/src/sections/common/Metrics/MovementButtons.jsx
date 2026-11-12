import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import { MOVEMENT_RULE_TEXT } from "./metricFormat";

const DIRS = {
  improved: { glyph: "↑", color: "green", word: "improved" },
  regressed: { glyph: "↓", color: "red", word: "regressed" },
};

/**
 * MovementButtons — `↑14 ↓7`: rows that improved / regressed vs the
 * baseline run (PRODUCT-SPEC §4, M6). Each count toggles a movement filter
 * pill; the active one turns solid. Unchanged, not comparable and the
 * movement rule live in the tooltip only.
 *
 * Props (frozen):
 * @param {number} props.improved
 * @param {number} props.regressed
 * @param {number} [props.unchanged]
 * @param {number} [props.notComparable]
 * @param {"improved"|"regressed"|null} [props.active]  the applied pill
 * @param {(dir: "improved"|"regressed") => void} [props.onSelect]
 *   omitted -> counts render as plain text
 */
const MovementButtons = ({
  improved,
  regressed,
  unchanged,
  notComparable,
  active,
  onSelect,
}) => {
  const tooltip = (
    <Stack spacing={0.5} sx={{ maxWidth: 320 }}>
      <Typography typography="s2">
        {[
          `${improved ?? 0} improved`,
          `${regressed ?? 0} regressed`,
          Number.isFinite(unchanged) ? `${unchanged} unchanged` : null,
          Number.isFinite(notComparable)
            ? `${notComparable} not comparable`
            : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </Typography>
      <Typography typography="s3" color="text.secondary">
        {MOVEMENT_RULE_TEXT}
      </Typography>
      {onSelect && (
        <Typography typography="s3" color="text.secondary">
          Click a count to show those rows.
        </Typography>
      )}
    </Stack>
  );

  const counts = { improved, regressed };

  return (
    <CustomTooltip show size="small" placement="top" arrow title={tooltip}>
      <Stack
        direction="row"
        gap={0.25}
        component="span"
        sx={{ display: "inline-flex" }}
      >
        {Object.entries(DIRS).map(([dir, d]) => {
          const n = Number.isFinite(counts[dir]) ? counts[dir] : 0;
          const isActive = active === dir;
          const disabled = !onSelect || (n === 0 && !isActive);
          return (
            <ButtonBase
              key={dir}
              disabled={disabled}
              aria-pressed={onSelect ? isActive : undefined}
              aria-label={`${n} ${d.word}${isActive ? ", filter on" : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                onSelect?.(dir);
              }}
              sx={(theme) => ({
                px: 0.5,
                borderRadius: 0.5,
                minWidth: 0,
                lineHeight: "18px",
                color: n === 0 ? "text.disabled" : theme.palette[d.color][600],
                bgcolor: isActive ? theme.palette[d.color][600] : "transparent",
                ...(isActive && { color: theme.palette.common.white }),
                "&:hover": {
                  bgcolor: isActive
                    ? theme.palette[d.color][700]
                    : alpha(theme.palette[d.color][500], 0.12),
                },
                "&.Mui-disabled": {
                  color:
                    n === 0 ? "text.disabled" : theme.palette[d.color][600],
                },
              })}
            >
              <Box
                component="span"
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                }}
              >
                {d.glyph}
                {n}
              </Box>
            </ButtonBase>
          );
        })}
      </Stack>
    </CustomTooltip>
  );
};

MovementButtons.propTypes = {
  improved: PropTypes.number,
  regressed: PropTypes.number,
  unchanged: PropTypes.number,
  notComparable: PropTypes.number,
  active: PropTypes.oneOf(["improved", "regressed", null]),
  onSelect: PropTypes.func,
};

export default MovementButtons;
