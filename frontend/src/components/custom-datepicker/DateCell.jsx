import React from "react";
import { isSameDay, isWithinInterval, isSameMonth, isToday } from "date-fns";
import { Box, ButtonBase } from "@mui/material";
import PropTypes from "prop-types";

export default function DateCell({ day, monthStart, range, onClick }) {
  const { start, end } = range;

  const isStart = start && isSameDay(day, start);
  const isEnd = end && isSameDay(day, end);
  const inRange = start && end && isWithinInterval(day, { start, end });
  const isCurrentMonth = isSameMonth(day, monthStart);
  const today = isToday(day);

  if (!isCurrentMonth) {
    return <Box sx={{ p: 0, textAlign: "center", color: "transparent" }}></Box>;
  }

  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        width: "100%",
        aspectRatio: "1 / 1",
        borderRadius: (() => {
          if (isStart) return "6px 0 0 6px";
          if (isEnd) return "0 6px 6px 0";
          if (inRange) return 0;
          if (today) return "50%";
          return "3px";
        })(),
        border: today ? "1px solid" : undefined,
        borderColor: today ? "text.primary" : undefined,
        bgcolor:
          isStart || isEnd
            ? "action.hover"
            : inRange
              ? "action.hover"
              : "transparent",
        color: isStart || isEnd ? "white" : "text.primary",
        "&:hover": {
          bgcolor:
            isStart || isEnd
              ? "action.hover"
              : inRange
                ? "action.hover"
                : "action.hover",
        },
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "12px", // reduced from 14px
        fontWeight: isStart || isEnd ? "bold" : "normal",
      }}
    >
      {day.getDate()}
    </ButtonBase>
  );
}

DateCell.propTypes = {
  day: PropTypes.instanceOf(Date).isRequired,
  monthStart: PropTypes.instanceOf(Date).isRequired,
  range: PropTypes.shape({
    start: PropTypes.instanceOf(Date),
    end: PropTypes.instanceOf(Date),
  }).isRequired,
  onClick: PropTypes.func.isRequired,
};
