import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * ObservationLevelsCell — shows span counts by level:
 *   🔴 1  ⚠️ 1  🔵 2
 *
 * Uses `data.observation_levels` if available (from backend),
 * otherwise falls back to `data.status` + total count.
 */
const ObservationLevelsCell = ({ data }) => {
  const levels = data?.observation_levels;

  // If backend provides level counts
  if (levels) {
    const errorCount = levels.error || 0;
    const warningCount = levels.warning || 0;
    const defaultCount = levels.default || levels.info || 0;

    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.5,
          height: "100%",
        }}
      >
        {errorCount > 0 && (
          <LevelBadge
            icon="solar:danger-circle-linear"
            color="status.danger.fg"
            count={errorCount}
          />
        )}
        {warningCount > 0 && (
          <LevelBadge
            icon="solar:danger-triangle-linear"
            color="status.warning.fg"
            count={warningCount}
          />
        )}
        {defaultCount > 0 && (
          <LevelBadge
            icon="solar:info-circle-linear"
            color="status.info.fg"
            count={defaultCount}
          />
        )}
      </Box>
    );
  }

  // Fallback — use status + total span count from row data
  const status = data?.status;
  const totalSpans = data?.total_spans ?? 1;
  const isError = status === "ERROR";

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        px: 1.5,
        height: "100%",
      }}
    >
      {isError && (
        <LevelBadge
          icon="solar:danger-circle-linear"
          color="status.danger.fg"
          count={1}
        />
      )}
      <LevelBadge
        icon="solar:info-circle-linear"
        color="status.info.fg"
        count={isError ? Math.max(0, totalSpans - 1) : totalSpans}
      />
    </Box>
  );
};

const LevelBadge = ({ icon, color, count }) => (
  <Box sx={{ display: "inline-flex", alignItems: "center", gap: "3px" }}>
    <Iconify icon={icon} width={14} sx={{ color }} />
    <Typography
      sx={{
        fontSize: 13,
        fontWeight: 500,
        color: "text.primary",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {count}
    </Typography>
  </Box>
);

LevelBadge.propTypes = {
  icon: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
  count: PropTypes.number.isRequired,
};

ObservationLevelsCell.propTypes = {
  data: PropTypes.object,
};

export default React.memo(ObservationLevelsCell);
