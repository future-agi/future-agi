import React from "react";
import { startOfMonth, startOfWeek, addDays, format } from "date-fns";
import DateCell from "./DateCell.jsx";
import { Box, Typography, IconButton } from "@mui/material";
import Iconify from "../iconify/iconify.jsx";
import PropTypes from "prop-types";

export default function Calendar({
  currentDate,
  range,
  onSelect,
  onPrevMonth,
  onNextMonth,
  maxDate,
}) {
  const monthStart = startOfMonth(currentDate);
  const startDate = startOfWeek(monthStart, { weekStartsOn: 0 });

  const rows = [];
  let day = startDate;

  for (let i = 0; i < 6; i++) {
    const days = [];
    for (let j = 0; j < 7; j++) {
      const cloneDay = day;
      days.push(
        <DateCell
          key={cloneDay}
          day={cloneDay}
          monthStart={monthStart}
          range={range}
          onClick={() => onSelect(cloneDay)}
          maxDate={maxDate}
        />,
      );
      day = addDays(day, 1);
    }
    rows.push(
      <Box
        key={i}
        display="grid"
        gridTemplateColumns="repeat(7, 1fr)"
        mb={0.5} // reduced spacing
      >
        {days}
      </Box>,
    );
  }

  return (
    <Box width="190px">
      {" "}
      {/* reduced from 252px */}
      {/* Header */}
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        mb={1}
      >
        <IconButton
          size="small"
          sx={{
            width: 24, // reduced width
            height: 24, // reduced height
            borderRadius: "4px",
            padding: "2px", // very tight padding
            border: "1px solid var(--border-light)",
          }}
          onClick={onPrevMonth}
        >
          <Iconify icon="ep:arrow-left" sx={{ fontSize: 14 }} />{" "}
          {/* smaller icon */}
        </IconButton>

        <Typography fontWeight="500" fontSize={12} color="text.primary">
          {format(monthStart, "MMMM yyyy")}
        </Typography>
        <IconButton
          sx={{
            width: 24, // reduced width
            height: 24, // reduced height
            borderRadius: "4px",
            padding: "2px", // very tight padding
            border: "1px solid var(--border-light)",
          }}
          onClick={onNextMonth}
        >
          <Iconify icon="ep:arrow-right" />
        </IconButton>
      </Box>
      {/* Weekdays */}
      <Box display="grid" gridTemplateColumns="repeat(7, 1fr)" mb={0.5}>
        {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((d) => (
          <Typography key={d} fontSize={11} align="center" color="text.primary">
            {d}
          </Typography>
        ))}
      </Box>
      {/* Dates */}
      {rows}
    </Box>
  );
}

Calendar.propTypes = {
  currentDate: PropTypes.instanceOf(Date).isRequired,
  range: PropTypes.shape({
    start: PropTypes.instanceOf(Date),
    end: PropTypes.instanceOf(Date),
  }),
  onSelect: PropTypes.func.isRequired,
  onPrevMonth: PropTypes.func.isRequired,
  onNextMonth: PropTypes.func.isRequired,
  maxDate: PropTypes.instanceOf(Date),
};
