import React, { useMemo } from "react";
import {
  Box,
  Divider,
  Stack,
  Typography,
  useTheme,
  alpha,
  Skeleton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useErrorFeedStats } from "src/api/errorFeed/error-feed";
import { useErrorFeedApiParams } from "../store";
import PropTypes from "prop-types";

const STAT_CARDS = [
  {
    key: "totalErrors",
    label: "Total errors",
    icon: "mdi:bug-outline",
    iconColor: "#938FA3",
    darkIconColor: "#71717a",
  },
  {
    key: "escalating",
    label: "Escalating",
    icon: "mdi:trending-up",
    iconColor: "#DB2F2D",
    darkIconColor: "#E87876",
    highlight: true,
  },
  {
    key: "acknowledged",
    label: "Acknowledged",
    icon: "mdi:check-circle-outline",
    iconColor: "#F5A623",
    darkIconColor: "#F5A623",
  },
  {
    key: "forReview",
    label: "For review",
    icon: "mdi:eye-outline",
    iconColor: "#2F7CF7",
    darkIconColor: "#78AAFA",
  },
  {
    key: "resolved",
    label: "Resolved",
    icon: "mdi:check-all",
    iconColor: "#5ACE6D",
    darkIconColor: "#5ACE6D",
  },
  {
    key: "affectedUsers",
    label: "Users affected",
    icon: "mdi:account-group-outline",
    iconColor: "#938FA3",
    darkIconColor: "#71717a",
  },
];

function StatCard({ stat, isDark, value, isLoading }) {
  const iconColor = isDark ? stat.darkIconColor : stat.iconColor;

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.25}
      data-testid={`error-feed-stat-${stat.key}`}
      sx={{ minWidth: 120 }}
    >
      <Box
        sx={{
          width: 30,
          height: 30,
          borderRadius: "6px",
          bgcolor: isDark ? alpha(iconColor, 0.12) : alpha(iconColor, 0.1),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Iconify icon={stat.icon} width={16} sx={{ color: iconColor }} />
      </Box>
      <Stack direction="column" gap={0}>
        {isLoading ? (
          <Skeleton width={40} height={22} />
        ) : (
          <Typography
            sx={{
              fontSize: "18px",
              fontWeight: 700,
              lineHeight: 1.2,
              color: stat.highlight ? iconColor : "text.primary",
              fontFeatureSettings: "'tnum'",
            }}
          >
            {(value ?? 0).toLocaleString()}
          </Typography>
        )}
        <Typography
          sx={{
            fontSize: "11px",
            fontWeight: 400,
            color: "text.disabled",
            lineHeight: 1.3,
            whiteSpace: "nowrap",
          }}
        >
          {stat.label}
        </Typography>
      </Stack>
    </Stack>
  );
}
StatCard.propTypes = {
  stat: PropTypes.object,
  isDark: PropTypes.bool,
  value: PropTypes.number,
  isLoading: PropTypes.bool,
};

export default function ErrorFeedStatsBar() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const apiParams = useErrorFeedApiParams();
  const statsParams = useMemo(() => {
    const nextParams = {};
    if (apiParams.project_id) nextParams.project_id = apiParams.project_id;
    if (apiParams.time_range_days) {
      nextParams.time_range_days = apiParams.time_range_days;
    }
    return nextParams;
  }, [apiParams.project_id, apiParams.time_range_days]);
  const { data: stats, isLoading } = useErrorFeedStats(statsParams);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0,
        px: 2,
        py: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: isDark ? "background.neutral" : "background.default",
        overflowX: "auto",
      }}
    >
      {STAT_CARDS.map((stat, i) => (
        <React.Fragment key={stat.key}>
          <StatCard
            stat={stat}
            isDark={isDark}
            value={stats?.[stat.key]}
            isLoading={isLoading}
          />
          {i < STAT_CARDS.length - 1 && (
            <Divider
              orientation="vertical"
              flexItem
              sx={{
                mx: 2.5,
                borderColor: "divider",
                alignSelf: "center",
                height: 28,
              }}
            />
          )}
        </React.Fragment>
      ))}
    </Box>
  );
}
