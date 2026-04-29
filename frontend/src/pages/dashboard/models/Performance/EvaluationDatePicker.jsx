import { Box, Button, Popover, useTheme } from "@mui/material";
import React, { useState } from "react";
import PropType from "prop-types";
import { DateField } from "@mui/x-date-pickers";
import {
  PickersDay,
  LocalizationProvider,
  DateCalendar,
} from "@mui/x-date-pickers";
import { AdapterDateFns } from "@mui/x-date-pickers/AdapterDateFns";
import { format, isValid, addMonths, isSameDay } from "date-fns";

const formatDate = (date) => format(date, "yyyy-MM-dd HH:mm:ss");

const EvaluationDatePicker = ({
  open,
  onClose,
  anchorEl,
  setDateFilter,
  setDateOption,
}) => {
  const [focusedElement, setFocusedElement] = useState("start");
  const [internalStartDate, setInternalStartDate] = useState(null);
  const [internalEndDate, setInternalEndDate] = useState(null);

  // For the two calendars, we use current month and next month
  const [leftMonth, setLeftMonth] = useState(new Date());
  const rightMonth = addMonths(leftMonth, 1);

  const handleMonthChange = (newMonth) => {
    setLeftMonth(newMonth);
  };
  const theme = useTheme();

  const isButtonActive = isValid(internalStartDate) && isValid(internalEndDate);

  // Custom day renderer to highlight selected dates
  const renderDay = (date, selectedDates, pickersDayProps) => {
    const isSelected =
      (internalStartDate && isSameDay(date, internalStartDate)) ||
      (internalEndDate && isSameDay(date, internalEndDate));

    const isHighlighted =
      internalStartDate &&
      internalEndDate &&
      date > internalStartDate &&
      date < internalEndDate;

    return (
      <PickersDay
        {...pickersDayProps}
        selected={isSelected}
        sx={{
          ...(isHighlighted && {
            backgroundColor: "rgba(25, 118, 210, 0.1)",
            "&:hover": {
              backgroundColor: "rgba(25, 118, 210, 0.2)",
            },
          }),
          ...(isSelected && {
            backgroundColor: "primary.main",
            color: "common.white",
            "&:hover": {
              backgroundColor: "primary.dark",
            },
          }),
        }}
      />
    );
  };

  // Handle date selection for either calendar
  const handleDateSelection = (newDate) => {
    if (focusedElement === "start" || !internalStartDate) {
      setInternalStartDate(newDate);
      setFocusedElement("end");
    } else if (focusedElement === "end") {
      // If the selected end date is before start date, swap them
      if (newDate < internalStartDate) {
        setInternalEndDate(internalStartDate);
        setInternalStartDate(newDate);
      } else {
        setInternalEndDate(newDate);
      }
    }
  };

  return (
    <Popover
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "left",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "left",
      }}
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
    >
      <Box
        sx={{
          padding: "16px",
          width: "680px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Date input fields */}
        <Box sx={{ display: "flex", gap: "12px", marginBottom: "16px" }}>
          <DateField
            fullWidth
            label="Start date"
            onFocus={() => setFocusedElement("start")}
            focused={focusedElement === "start"}
            value={internalStartDate}
            onChange={(newValue) => setInternalStartDate(newValue)}
            format="dd-MM-yyyy"
          />
          <DateField
            fullWidth
            label="End date"
            onFocus={() => setFocusedElement("end")}
            focused={focusedElement === "end"}
            value={internalEndDate}
            onChange={(newValue) => setInternalEndDate(newValue)}
            format="dd-MM-yyyy"
          />
        </Box>

        {/* Dual calendars */}
        <Box
          sx={{
            display: "flex",
            borderTop: "1px solid var(--border-default)",
            paddingTop: "8px",
          }}
        >
          <LocalizationProvider dateAdapter={AdapterDateFns}>
            <Box
              sx={{
                display: "flex",
                width: "100%",
                justifyContent: "space-between",
                position: "relative",
              }}
            >
              {/* Left calendar */}
              <Box
                sx={{
                  flexBasis: "50%",
                  borderRight: "1px dotted var(--border-default)",
                  paddingRight: "8px",
                }}
              >
                <DateCalendar
                  views={["day"]}
                  openTo="day"
                  value={internalStartDate}
                  onChange={handleDateSelection}
                  renderDay={renderDay}
                  defaultCalendarMonth={leftMonth}
                  onMonthChange={handleMonthChange}
                  sx={{
                    "& .MuiPickersCalendarHeader-label": {
                      textAlign: "center",
                      width: "100%",
                    },
                  }}
                />
              </Box>

              {/* Right calendar */}
              <Box sx={{ flexBasis: "50%", paddingLeft: "8px" }}>
                <DateCalendar
                  views={["day"]}
                  openTo="day"
                  value={internalEndDate}
                  onChange={handleDateSelection}
                  renderDay={renderDay}
                  defaultCalendarMonth={rightMonth}
                  minDate={internalStartDate || undefined}
                  sx={{
                    "& .MuiPickersCalendarHeader-label": {
                      textAlign: "center",
                      width: "100%",
                    },
                  }}
                />
              </Box>
            </Box>
          </LocalizationProvider>
        </Box>

        {/* Apply button */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            width: "100%",
            marginTop: theme.spacing(2),
            gap: theme.spacing(1),
          }}
        >
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onClose}
            sx={{ width: theme.spacing(12), color: "text.disabled" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!isButtonActive}
            sx={{ width: theme.spacing(12) }}
            onClick={() => {
              setDateFilter([
                formatDate(internalStartDate),
                formatDate(internalEndDate),
              ]);
              setDateOption("Custom");
              onClose();
            }}
          >
            Apply
          </Button>
        </Box>
      </Box>
    </Popover>
  );
};

EvaluationDatePicker.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
  anchorEl: PropType.any,
  setDateFilter: PropType.func,
  setDateOption: PropType.func,
};

export default EvaluationDatePicker;
