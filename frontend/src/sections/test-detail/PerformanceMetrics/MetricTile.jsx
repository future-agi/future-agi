import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color/svg-color";
import CustomTooltip from "src/components/tooltip";

/**
 * One metric, drawn the same way everywhere on this screen.
 *
 * Performance Metrics had three cards speaking three visual languages: Call
 * Details put its number inside a fixed 63px bordered pill with the label
 * beside it, System Metrics used an icon tile with the number below the label,
 * and neither aligned with the other. Three ways to read a number in one row
 * of cards is most of why the section looked unfinished.
 *
 * Label above, value below, context under that — the value is the thing being
 * compared, so it gets the size and the tabular figures that let a column of
 * them line up. Clickable tiles say so on hover rather than by looking
 * different at rest, which is what made the filterable rows in Call Details
 * read as a different kind of object from the one beside them.
 */
export default function MetricTile({
  label,
  value,
  suffix = "",
  subtext,
  icon,
  iconColor,
  tooltip,
  onClick,
}) {
  return (
    <Stack
      direction="row"
      gap={1.25}
      onClick={onClick}
      sx={{
        alignItems: "flex-start",
        cursor: onClick ? "pointer" : "default",
        borderRadius: 1,
        p: 1,
        m: -1,
        transition: (t) => t.transitions.create("background-color", { duration: 150 }),
        ...(onClick && { "&:hover": { bgcolor: "action.hover" } }),
      }}
    >
      {icon && (
        <Box
          sx={{
            height: 30,
            width: 30,
            flexShrink: 0,
            borderRadius: 0.75,
            bgcolor: "background.neutral",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor sx={{ height: 15, width: 15, bgcolor: iconColor || "text.subtitle" }} src={icon} />
        </Box>
      )}

      <Box minWidth={0}>
        <Stack direction="row" alignItems="center" gap={0.375}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: "fontWeightBold",
              color: "text.subtitle",
              letterSpacing: 0.3,
              textTransform: "uppercase",
            }}
          >
            {label}
          </Typography>
          {tooltip && (
            <CustomTooltip size="small" show title={tooltip}>
              <SvgColor
                sx={{ height: 12, width: 12, cursor: "pointer", flexShrink: 0, bgcolor: "text.disabled" }}
                src="/assets/icons/ic_info.svg"
              />
            </CustomTooltip>
          )}
        </Stack>

        <Typography
          sx={{
            typography: "m1",
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
            lineHeight: 1.25,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value ?? "—"}
          {value != null && suffix ? (
            <Typography
              component="span"
              sx={{ typography: "s2", color: "text.subtitle", ml: 0.25, fontWeight: "fontWeightRegular" }}
            >
              {suffix}
            </Typography>
          ) : null}
        </Typography>

        {subtext && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{subtext}</Typography>
        )}
      </Box>
    </Stack>
  );
}

MetricTile.propTypes = {
  label: PropTypes.string,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  suffix: PropTypes.string,
  subtext: PropTypes.string,
  icon: PropTypes.string,
  iconColor: PropTypes.string,
  tooltip: PropTypes.string,
  onClick: PropTypes.func,
};
