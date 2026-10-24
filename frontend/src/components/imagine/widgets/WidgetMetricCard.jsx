/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function WidgetMetricCard({ config }) {
  const { value, subtitle, trend, trendDirection, icon } = config;

  const trendColor =
    trendDirection === "up"
      ? "status.success.fg"
      : trendDirection === "down"
        ? "status.danger.fg"
        : "text.tertiary";
  const trendIcon =
    trendDirection === "up"
      ? "solar:graph-up-linear"
      : trendDirection === "down"
        ? "solar:graph-down-linear"
        : "solar:minus-linear";

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: 0.5,
        px: 2,
      }}
    >
      {icon && (
        <Iconify
          icon={icon}
          width={28}
          sx={{ color: "text.secondary", mb: 0.5 }}
        />
      )}

      <Typography
        sx={{
          fontSize: 32,
          fontWeight: 600,
          lineHeight: 1.1,
          color: "text.primary",
          fontFamily: "'IBM Plex Mono', monospace",
        }}
      >
        {value ?? "—"}
      </Typography>

      {subtitle && (
        <Typography
          variant="body2"
          sx={{ fontSize: 12, color: "text.secondary", textAlign: "center" }}
        >
          {subtitle}
        </Typography>
      )}

      {trend && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.25 }}>
          <Iconify icon={trendIcon} width={16} sx={{ color: trendColor }} />
          <Typography sx={{ fontSize: 12, fontWeight: 600, color: trendColor }}>
            {trend}
          </Typography>
        </Box>
      )}
    </Box>
  );
}

WidgetMetricCard.propTypes = { config: PropTypes.object.isRequired };
