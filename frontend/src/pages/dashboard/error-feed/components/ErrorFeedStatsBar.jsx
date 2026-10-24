import React from "react";
import { Box, Divider, Stack, Typography, Skeleton } from "@mui/material";
import Iconify from "src/components/iconify";
import { useErrorFeedStats } from "src/api/errorFeed/mockAdapter";
import { useErrorFeedApiParams } from "../store";
import PropTypes from "prop-types";

const STAT_CARDS = [
  {
    key: "totalErrors",
    label: "Total errors",
    icon: "solar:bug-linear",
    role: "neutral",
  },
  {
    key: "escalating",
    label: "Escalating",
    icon: "solar:graph-up-linear",
    role: "danger",
    highlight: true,
  },
  {
    key: "acknowledged",
    label: "Acknowledged",
    icon: "solar:check-circle-linear",
    role: "warning",
  },
  {
    key: "forReview",
    label: "For review",
    icon: "solar:eye-linear",
    role: "info",
  },
  {
    key: "resolved",
    label: "Resolved",
    icon: "solar:check-read-linear",
    role: "success",
  },
  {
    key: "affectedUsers",
    label: "Users affected",
    icon: "solar:users-group-rounded-linear",
    role: "neutral",
  },
];

function StatCard({ stat, value, isLoading }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.25}
      sx={{ minWidth: 120 }}
    >
      <Box
        sx={{
          width: 30,
          height: 30,
          borderRadius: "6px",
          bgcolor: `status.${stat.role}.bg`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Iconify
          icon={stat.icon}
          width={16}
          sx={{ color: `status.${stat.role}.fg` }}
        />
      </Box>
      <Stack direction="column" gap={0}>
        {isLoading ? (
          <Skeleton width={40} height={22} />
        ) : (
          <Typography
            sx={{
              fontSize: "16px",
              fontWeight: 600,
              lineHeight: 1.2,
              color: stat.highlight ? `status.${stat.role}.fg` : "text.primary",
              fontFeatureSettings: "'tnum'",
            }}
          >
            {(value ?? 0).toLocaleString()}
          </Typography>
        )}
        <Typography
          sx={{
            fontSize: "12px",
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
  value: PropTypes.number,
  isLoading: PropTypes.bool,
};

export default function ErrorFeedStatsBar() {
  const apiParams = useErrorFeedApiParams();
  const { data: stats, isLoading } = useErrorFeedStats(apiParams);

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
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflowX: "auto",
      }}
    >
      {STAT_CARDS.map((stat, i) => (
        <React.Fragment key={stat.key}>
          <StatCard
            stat={stat}
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
