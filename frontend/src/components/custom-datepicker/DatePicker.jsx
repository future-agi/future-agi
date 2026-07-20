import React, { useState } from "react";
import { addMonths, subMonths, format, parseISO, isValid } from "date-fns";
import Calendar from "./Calender.jsx";
import { Box, Button, Divider, Popover } from "@mui/material";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2.jsx";
import PropType from "prop-types";
import { getMaxSelectableDate, isDateSelectable } from "./dateRangeLimits";

export default function CustomDateRangePicker({
  open,
  onClose,
  anchorEl,
  setDateFilter,
  setDateOption,
}) {
  const { control } = useForm();
  const [range, setRange] = useState({ start: null, end: null });
  const [currentDate1, setCurrentDate1] = useState(new Date());
  const [currentDate2, setCurrentDate2] = useState(addMonths(new Date(), 1));
  const maxSelectableDate = getMaxSelectableDate();

  const handleSelectDate = (date) => {
    if (!isDateSelectable(date, maxSelectableDate)) return;

    if (!range.start || (range.start && range.end)) {
      setRange({ start: date, end: null });
    } else if (range.start && !range.end) {
      if (date < range.start) {
        setRange({ start: date, end: range.start });
      } else {
        setRange({ ...range, end: date });
      }
    }
  };

  const handleInputChange = (value, type) => {
    const parsedDate = parseISO(value);
    if (!isValid(parsedDate)) return;
    if (!isDateSelectable(parsedDate, maxSelectableDate)) return;

    setRange((prev) => {
      if (type === "start") {
        if (prev.end && parsedDate > prev.end) {
          return { start: prev.end, end: parsedDate };
        }
        return { ...prev, start: parsedDate };
      }

      if (type === "end") {
        if (prev.start && parsedDate < prev.start) {
          return { start: parsedDate, end: prev.start };
        }
        return { ...prev, end: parsedDate };
      }

      return prev;
    });
  };

  const handleCustomDate = () => {
    if (!range.start || !range.end) {
      alert("Please select both start and end dates.");
      return;
    }
    if (range.start > range.end) {
      alert("Start date cannot be after end date.");
      return;
    }
    setDateFilter([
      format(range.start, "yyyy-MM-dd HH:mm:ss"),
      format(range.end, "yyyy-MM-dd HH:mm:ss"),
    ]);
    setDateOption("Custom");
    onClose();
  };

  const isInvalidRange = range.start && range.end && range.start > range.end;
  const isButtonActive =
    isValid(range.start) && isValid(range.end) && !isInvalidRange;

  return (
    <Popover
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      elevation={3}
    >
      <Box
        sx={{
          p: 1,
          display: "flex",
          flexDirection: "column",
          gap: 1.5,

          bgcolor: "background.paper",
        }}
      >
        {/* Calendars with Inputs */}
        <Box display="flex" gap={1.5}>
          {/* Left Calendar */}
          <Box display="flex" flexDirection="column" gap={1.5}>
            <FormTextFieldV2
              control={control}
              fieldName="startDate"
              label="Start Date"
              fieldType="date"
              size="small"
              fullWidth
              value={range.start ? format(range.start, "yyyy-MM-dd") : ""}
              onChange={(e) => handleInputChange(e.target.value, "start")}
              error={isInvalidRange}
              helperText={
                isInvalidRange ? "Start date must be before end date" : ""
              }
            />
            <Calendar
              currentDate={currentDate1}
              onPrevMonth={() => {
                setCurrentDate1((prev) => subMonths(prev, 1));
                setCurrentDate2((prev) => subMonths(prev, 1));
              }}
              onNextMonth={() => {
                setCurrentDate1((prev) => addMonths(prev, 1));
                setCurrentDate2((prev) => addMonths(prev, 1));
              }}
              range={range}
              onSelect={handleSelectDate}
              maxDate={maxSelectableDate}
            />
          </Box>

          {/* Divider */}
          <Divider
            orientation="vertical"
            flexItem
            sx={{ borderColor: "divider" }}
          />

          {/* Right Calendar */}
          <Box display="flex" flexDirection="column" gap={1.5}>
            <FormTextFieldV2
              control={control}
              fieldName="endDate"
              label="End Date"
              fieldType="date"
              size="small"
              fullWidth
              value={range.end ? format(range.end, "yyyy-MM-dd") : ""}
              onChange={(e) => handleInputChange(e.target.value, "end")}
              error={isInvalidRange}
              helperText={
                isInvalidRange ? "End date must be after start date" : ""
              }
            />
            <Calendar
              currentDate={currentDate2}
              onPrevMonth={() => {
                setCurrentDate1((prev) => subMonths(prev, 1));
                setCurrentDate2((prev) => subMonths(prev, 1));
              }}
              onNextMonth={() => {
                setCurrentDate1((prev) => addMonths(prev, 1));
                setCurrentDate2((prev) => addMonths(prev, 1));
              }}
              range={range}
              onSelect={handleSelectDate}
              maxDate={maxSelectableDate}
            />
          </Box>
        </Box>

        {/* Divider */}
        <Divider
          orientation="horizontal"
          flexItem
          sx={{ borderColor: "divider" }}
        />

        {/* Buttons */}
        <Box display="flex" justifyContent="flex-end" gap={1}>
          <Button size="small" variant="outlined" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={handleCustomDate}
            disabled={!isButtonActive}
          >
            Done
          </Button>
        </Box>
      </Box>
    </Popover>
  );
}

CustomDateRangePicker.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
  anchorEl: PropType.any,
  setDateFilter: PropType.func,
  setDateOption: PropType.func,
};
