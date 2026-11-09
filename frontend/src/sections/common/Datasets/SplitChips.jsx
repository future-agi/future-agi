import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography, useTheme } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { splitChipColors } from "./splitColors";

/** One split as a small tinted chip. */
export const SplitChip = ({ split, size = "small", onClick }) => {
  const theme = useTheme();
  const c = splitChipColors(split?.color, theme);
  const small = size === "small";
  return (
    <Chip
      size="small"
      label={split?.name}
      onClick={
        onClick
          ? (e) => {
              e.stopPropagation();
              onClick(split);
            }
          : undefined
      }
      sx={{
        height: small ? 20 : 24,
        borderRadius: 0.5,
        bgcolor: c.bg,
        color: c.fg,
        // DL#47: an outline keeps the chip visible without relying on hue.
        border: "1px solid",
        borderColor: c.border,
        typography: small ? "s3" : "s2",
        fontWeight: 500,
        maxWidth: 140,
        flexShrink: 0,
        "& .MuiChip-label": { px: small ? 0.75 : 1 },
        "&:hover": { bgcolor: c.border },
      }}
    />
  );
};

SplitChip.propTypes = {
  split: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    color: PropTypes.string,
  }).isRequired,
  size: PropTypes.oneOf(["small", "medium"]),
  onClick: PropTypes.func,
};

/**
 * SplitChips — a row's or a run's splits (PRODUCT-SPEC §4, C13). Shows the
 * first `max`, then `+N` with the rest in the tooltip.
 *
 * Props (frozen):
 * @param {Array<{id: string, name: string, color: string}>} props.splits
 * @param {number} [props.max=2]
 * @param {"small"|"medium"} [props.size="small"]
 * @param {(split: object) => void} [props.onClick]  each chip becomes a button
 */
const SplitChips = ({ splits, max = 2, size = "small", onClick }) => {
  const theme = useTheme();
  const list = (splits ?? []).filter((s) => s?.name);
  if (!list.length) return null;
  const shown = list.slice(0, Math.max(0, max));
  const rest = list.slice(shown.length);
  return (
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
      {shown.map((s) => (
        <SplitChip
          key={s.id ?? s.name}
          split={s}
          size={size}
          onClick={onClick}
        />
      ))}
      {rest.length > 0 && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={
            <Stack spacing={0.5}>
              {rest.map((s) => (
                <Stack
                  key={s.id ?? s.name}
                  direction="row"
                  alignItems="center"
                  gap={0.75}
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: "2px",
                      bgcolor: splitChipColors(s.color, theme).dot,
                    }}
                  />
                  <Typography typography="s2">{s.name}</Typography>
                </Stack>
              ))}
            </Stack>
          }
        >
          <Typography
            component="span"
            typography="s3"
            color="text.secondary"
            sx={{
              px: 0.5,
              borderRadius: 0.5,
              bgcolor: "action.hover",
              lineHeight: size === "small" ? "20px" : "24px",
              flexShrink: 0,
              cursor: "default",
            }}
          >
            +{rest.length}
          </Typography>
        </CustomTooltip>
      )}
    </Stack>
  );
};

SplitChips.propTypes = {
  splits: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      color: PropTypes.string,
    }),
  ),
  max: PropTypes.number,
  size: PropTypes.oneOf(["small", "medium"]),
  onClick: PropTypes.func,
};

export default SplitChips;
