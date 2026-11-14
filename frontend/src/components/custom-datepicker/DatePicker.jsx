import React, { useState, useEffect, useRef } from "react";
import {
  addDays,
  addMonths,
  subMonths,
  format,
  parseISO,
  isValid,
  startOfDay,
  subMilliseconds,
} from "date-fns";
import Calendar from "./Calender.jsx";
import { Box, Button, Divider, Popover } from "@mui/material";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2.jsx";
import PropType from "prop-types";

export default function CustomDateRangePicker({
  open,
  onClose,
  anchorEl,
  setDateFilter,
  setDateOption,
  value,
  rangeConvention = "half-open",
}) {
  const { control } = useForm();
  const [range, setRange] = useState({ start: null, end: null });
  const [currentDate1, setCurrentDate1] = useState(new Date());
  const [currentDate2, setCurrentDate2] = useState(addMonths(new Date(), 1));

  // Seed only on the open false→true transition so a caller passing a fresh
  // value array each render can't clobber an in-progress selection.
  const prevOpenRef = useRef(false);
  useEffect(() => {
    const justOpened = open && !prevOpenRef.current;
    prevOpenRef.current = open;
    if (!justOpened) return;
    setRange({ start: null, end: null });
    if (!value?.[0] || !value?.[1]) return;
    const start = new Date(value[0]);
    const end = new Date(value[1]);
    if (
      !isValid(start) ||
      !isValid(end) ||
      end < start ||
      (rangeConvention === "half-open" && +end === +start)
    )
      return;
    // Preserve exact stored timestamps until the user makes a date selection.
    // Performance alone uses inclusive local dates; Observe uses half-open bounds.
    setRange({
      start: startOfDay(start),
      end: startOfDay(
        rangeConvention === "inclusive-local" ? end : subMilliseconds(end, 1),
      ),
      original: value,
    });
    setCurrentDate1(start);
    setCurrentDate2(addMonths(start, 1));
  }, [open, value, rangeConvention]);

  const handleSelectDate = (date) => {
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
    if (!isValid(parsedDate)) {
      setRange((prev) => ({ ...prev, [type]: null, original: null }));
      return;
    }

    setRange((prev) => {
      if (type === "start") {
        if (prev.end && parsedDate > prev.end) {
          return { start: prev.end, end: parsedDate };
        }
        return { ...prev, start: parsedDate, original: null };
      }

      if (type === "end") {
        if (prev.start && parsedDate < prev.start) {
          return { start: parsedDate, end: prev.start };
        }
        return { ...prev, end: parsedDate, original: null };
      }

      return prev;
    });
  };

  const isInvalidRange = Boolean(
    range.start && range.end && range.start > range.end,
  );
  const isButtonActive =
    isValid(range.start) && isValid(range.end) && !isInvalidRange;

  const handleCustomDate = () => {
    if (!isButtonActive) return;
    // Performance's backend parses local timestamps and adds the day itself.
    // Observe uses ISO instants with a calendar-day (not 24-hour) exclusive end.
    setDateFilter(
      range.original ||
        (rangeConvention === "inclusive-local"
          ? [
              format(range.start, "yyyy-MM-dd HH:mm:ss"),
              format(range.end, "yyyy-MM-dd HH:mm:ss"),
            ]
          : [
              startOfDay(range.start).toISOString(),
              addDays(startOfDay(range.end), 1).toISOString(),
            ]),
    );
    setDateOption("Custom");
    onClose();
  };

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
  rangeConvention: PropType.oneOf(["half-open", "inclusive-local"]),
  value: PropType.arrayOf(
    PropType.oneOfType([PropType.instanceOf(Date), PropType.string]),
  ),
};
