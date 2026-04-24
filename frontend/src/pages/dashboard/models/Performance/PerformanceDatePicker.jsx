import { Box, Button, Popover } from "@mui/material";
import React, { useMemo, useState } from "react";
import PropType from "prop-types";
import { DateField, DateCalendar } from "@mui/x-date-pickers";
import { format, isValid } from "date-fns";

const formatDate = (date) => format(date, "yyyy-MM-dd HH:mm:ss");

const PerformanceDatePicker = ({
  open,
  onClose,
  anchorEl,
  setDateFilter,
  setDateOption,
  setStartDate,
  setEndDate,
}) => {
  const [focusedElement, setFocusedElement] = useState("start");
  const [internalStartDate, setInternalStartDate] = useState(null);
  const [internalEndDate, setInternalEndDate] = useState(null);

  const calendarValue = useMemo(() => {
    if (focusedElement === "start") return internalStartDate;
    if (focusedElement === "end") return internalEndDate;
  }, [internalStartDate, internalEndDate, focusedElement]);

  const isButtonActive = isValid(internalStartDate) && isValid(internalEndDate);

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
          padding: "10px",
          width: "340px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", gap: "12px" }}>
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
        <DateCalendar
          value={calendarValue}
          onChange={(v) => {
            if (focusedElement === "start") setInternalStartDate(v);
            if (focusedElement === "end") setInternalEndDate(v);
          }}
        />
        <Box
          sx={{ display: "flex", justifyContent: "flex-end", width: "100%" }}
        >
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!isButtonActive}
            onClick={() => {
              setStartDate(internalStartDate);
              setEndDate(internalEndDate);
              setDateFilter([
                formatDate(internalStartDate),
                formatDate(internalEndDate),
              ]);

              // trackEvent(Events.metricPerformanceDateRangeChange, {
              //   "Old Start Date": dateFilter?.[0],
              //   "Old End Date": dateFilter?.[1],
              //   "New Start Date": formatDate(internalStartDate),
              //   "New End Date": formatDate(internalEndDate),
              // });
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

PerformanceDatePicker.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
  anchorEl: PropType.any,
  setDateFilter: PropType.func,
  setDateOption: PropType.func,
  setStartDate: PropType.func,
  setEndDate: PropType.func,
};

export default PerformanceDatePicker;
