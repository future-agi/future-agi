import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color/svg-color";
import { getChatOverrides, getIcon, getLabel, getSuffix } from "./common";
import { AGENT_TYPES } from "src/sections/agents/constants";

/**
 * The four numbers worth reading first.
 *
 * Performance Metrics used to be three cards of equal visual weight, so how
 * many calls ran sat at the same size as the agent's words-per-minute, and the
 * Call Details card carried three tiles in a box tall enough for eight. This
 * is the answer to "how did the run go" — full width, no card chrome, divided
 * rather than boxed — and the cards below it become the detail they always
 * were.
 *
 * The call-detail entries stay clickable: they filter the table underneath,
 * which is the one interaction on this whole section and was previously
 * indistinguishable from the tiles that do nothing.
 */
export default function HeadlineStats({ callDetails, systemMetrics, agentType, onFilter }) {
  const chat = agentType === AGENT_TYPES.CHAT;

  /* Two from the call counts, two from the system metrics — whichever of the
     headline system metrics this agent type actually reports. */
  const headlineKeys = chat
    ? ["avg_csat_score", "avg_chat_latency_ms"]
    : ["avg_score", "avg_agent_latency"];

  const counts = Object.entries(callDetails || {});

  const stats = [
    ...counts.map(([key, value]) => ({
        key,
        label: (chat && getChatOverrides[key]?.label) || getLabel(key),
        icon: (chat && getChatOverrides[key]?.icon) || getIcon(key),
        suffix: (chat && getChatOverrides[key]?.suffix) || getSuffix(key),
        value,
        onClick: () => onFilter?.(key),
      })),
    ...headlineKeys
      .filter((key) => systemMetrics?.[key] !== undefined)
      .slice(0, Math.max(1, 4 - counts.length))
      .map((key) => ({
        key,
        label: getLabel(key),
        icon: getIcon(key),
        suffix: getSuffix(key),
        value: systemMetrics[key],
      })),
  ];

  if (!stats.length) return null;

  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {stats.map((s, i) => (
        <Box
          key={s.key}
          onClick={s.onClick}
          sx={{
            flex: 1,
            minWidth: 0,
            px: 2.5,
            py: 2,
            cursor: s.onClick ? "pointer" : "default",
            borderLeft: { sm: i === 0 ? "none" : "1px solid" },
            borderTop: { xs: i === 0 ? "none" : "1px solid", sm: "none" },
            borderColor: { xs: "divider", sm: "divider" },
            transition: (t) => t.transitions.create("background-color", { duration: 150 }),
            ...(s.onClick && { "&:hover": { bgcolor: "action.hover" } }),
          }}
        >
          <Stack direction="row" alignItems="center" gap={0.75}>
            {s.icon && (
              <SvgColor sx={{ height: 14, width: 14, bgcolor: "text.subtitle", flexShrink: 0 }} src={s.icon} />
            )}
            <Typography
              noWrap
              sx={{
                typography: "s3",
                fontWeight: "fontWeightBold",
                color: "text.subtitle",
                letterSpacing: 0.3,
                textTransform: "uppercase",
              }}
            >
              {s.label}
            </Typography>
          </Stack>
          <Typography
            sx={{
              typography: "l3",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
              lineHeight: 1.2,
              mt: 0.5,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {s.value ?? "—"}
            {s.value != null && s.suffix ? (
              <Typography
                component="span"
                sx={{ typography: "s1", color: "text.subtitle", ml: 0.5, fontWeight: "fontWeightRegular" }}
              >
                {s.suffix}
              </Typography>
            ) : null}
          </Typography>
          {s.onClick && (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              Filters the table below
            </Typography>
          )}
        </Box>
      ))}
    </Stack>
  );
}

HeadlineStats.propTypes = {
  callDetails: PropTypes.object,
  systemMetrics: PropTypes.object,
  agentType: PropTypes.string,
  onFilter: PropTypes.func,
};
