import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";

const TalkRatioCell = (params) => {
  const data = params?.data;
  const ratio = data?.talk_ratio;
  if (ratio == null) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.disabled", px: 2 }}
      >
        -
      </Typography>
    );
  }

  // Backend may return talk_ratio as a scalar (bot_time / user_time) from
  // trace.py:480 or as an object {user, bot, user_pct, bot_pct} from
  // observability_providers.py. Normalize both to userPct/botPct.
  let userPct;
  let botPct;
  let userSec;
  let botSec;
  if (typeof ratio === "number") {
    botPct = Math.round((ratio / (ratio + 1)) * 100);
    userPct = 100 - botPct;
  } else {
    userPct = ratio.user_pct ?? 0;
    botPct = ratio.bot_pct ?? 0;
    userSec = ratio.user;
    botSec = ratio.bot;
  }

  const tooltip =
    userSec != null || botSec != null
      ? `User: ${userSec ?? 0}s (${userPct}%) | Bot: ${botSec ?? 0}s (${botPct}%)`
      : `User: ${userPct}% | Bot: ${botPct}%`;

  return (
    <Tooltip title={tooltip} arrow placement="bottom">
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 2,
          height: "100%",
        }}
      >
        {/* Stacked bar */}
        <Box
          sx={{
            display: "flex",
            width: 60,
            height: 6,
            borderRadius: 3,
            overflow: "hidden",
            bgcolor: "divider",
          }}
        >
          <Box
            sx={{
              width: `${userPct}%`,
              bgcolor: "info.main",
              transition: "width 200ms",
            }}
          />
          <Box
            sx={{
              width: `${botPct}%`,
              bgcolor: "secondary.main",
              transition: "width 200ms",
            }}
          />
        </Box>
        <Typography
          variant="body2"
          sx={{ fontSize: 11, color: "text.secondary", whiteSpace: "nowrap" }}
        >
          {userPct}:{botPct}
        </Typography>
      </Box>
    </Tooltip>
  );
};

TalkRatioCell.propTypes = { data: PropTypes.object };

export default React.memo(TalkRatioCell);
