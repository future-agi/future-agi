import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const TalkRatioCell = (params) => {
  const data = params?.data;
  // Backend-sourced integer split (user_talk_pct / bot_talk_pct) — no client-side rounding.
  const userPct = data?.user_talk_pct;
  const botPct = data?.bot_talk_pct;
  if (userPct == null || botPct == null) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.disabled", px: 2 }}
      >
        -
      </Typography>
    );
  }

  const tooltip = `User: ${userPct}% | Bot: ${botPct}%`;

  return (
    <CustomTooltip title={tooltip} arrow placement="bottom" show>
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
    </CustomTooltip>
  );
};

TalkRatioCell.propTypes = { data: PropTypes.object };

export default React.memo(TalkRatioCell);
