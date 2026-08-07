/* eslint-disable react-refresh/only-export-components */
import { Button, ToggleButton, ToggleButtonGroup } from "@mui/material";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";

const formatDate = (date) => {
  return date.toISOString().split("T")[0]; // Format as 'YYYY-MM-DD'
};

export function getDateRange(range, reference = new Date()) {
  const toDate = new Date(reference); // 'to' date is today
  const fromDate = new Date(reference); // Start with today for 'from' date

  switch (range) {
    case "today":
      // 1 day means today, so 'from' date is the same as 'to' date
      break;
    case "yesterday":
      fromDate.setDate(fromDate.getDate() - 1);
      toDate.setDate(toDate.getDate() - 1);
      break;
    case "7d":
      fromDate.setDate(toDate.getDate() - 6); // Subtract 6 days (7 days including today)
      break;
    case "30d":
      fromDate.setDate(toDate.getDate() - 29); // Subtract 29 days
      break;
    case "1m":
      fromDate.setMonth(toDate.getMonth() - 1); // Subtract 1 month
      break;
    case "3m":
      fromDate.setMonth(toDate.getMonth() - 3); // Subtract 3 months
      break;
    case "6m":
      fromDate.setMonth(toDate.getMonth() - 6); // Subtract 6 months
      break;
    case "12m":
      fromDate.setFullYear(toDate.getFullYear() - 1); // Subtract 1 year
      break;
    default:
      return "Invalid range"; // Return an error message for invalid input
  }

  return `${formatDate(fromDate)}:${formatDate(toDate)}`;
}

export default function InsightsDateSelector({ dates, onDateChange }) {
  const datePopover = usePopover();
  const [selectedTab, setSelectedTab] = useState(dates.selectedTab);

  const [startDate, setStartDate] = useState(null);
  const [endDate, setEndDate] = useState(null);
  const [selectedDateRange, setSelectedDateRange] = useState(dates.dateValues);

  const handleApply = () => {
    setSelectedDateRange(
      `${startDate.toDateString()} - ${endDate.toDateString()}`,
    );
    setSelectedTab("custom");
    onDateChange((prevValue) => ({
      ...prevValue,
      dateValues: `${formatDate(startDate)}:${formatDate(endDate)}`,
      selectedTab: "custom",
    }));
    datePopover.setOpen(null);
  };

  const handleCancel = () => {
    datePopover.setOpen(null);
  };

  function handleTabChange(event, newValue) {
    if (!newValue || newValue == "custom") {
      return;
    }
    setSelectedDateRange(null);
    setSelectedTab(newValue);
    // dates.dateValues = getDateRange(newValue);
    // dates.selectedTab = newValue;
    onDateChange((prevValue) => ({
      ...prevValue,
      dateValues: getDateRange(newValue),
      selectedTab: newValue,
    }));
  }

  return (
    <>
      <ToggleButtonGroup
        exclusive
        value={selectedTab}
        onChange={handleTabChange}
        aria-label="left side button group"
        size="small"
      >
        <ToggleButton onClick={datePopover.onOpen} key="custom" value="custom">
          <Iconify icon="uil:calender" />
          {selectedTab === "custom" ? selectedDateRange : "Custom"}
        </ToggleButton>
        <ToggleButton key="today" value="today">
          Today
        </ToggleButton>
        <ToggleButton key="yesterday" value="yesterday">
          Yesterday
        </ToggleButton>
        <ToggleButton key="7d" value="7d">
          7D
        </ToggleButton>
        <ToggleButton key="30d" value="30d">
          30D
        </ToggleButton>
        <ToggleButton key="3m" value="3m">
          3M
        </ToggleButton>
        <ToggleButton key="6m" value="6m">
          6M
        </ToggleButton>
        <ToggleButton key="12m" value="12m">
          12M
        </ToggleButton>
      </ToggleButtonGroup>

      <CustomPopover
        open={datePopover.open}
        onClose={datePopover.onClose}
        sx={{ width: 400 }}
      >
        {/* <MenuItem> */}
        <DatePicker
          label="Start date"
          value={startDate}
          onChange={(newValue) => {
            setStartDate(newValue);
          }}
          slotProps={{ textField: { fullWidth: true } }}
          sx={{
            maxWidth: { md: 180 },
          }}
        />
        <DatePicker
          label="Start date"
          value={endDate}
          onChange={(newValue) => {
            setEndDate(newValue);
          }}
          slotProps={{ textField: { fullWidth: true } }}
          sx={{
            maxWidth: { md: 180 },
          }}
        />
        <Button onClick={handleCancel}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleApply}
          disabled={!startDate || !endDate}
        >
          Apply
        </Button>
        {/* </MenuItem> */}
      </CustomPopover>
    </>
  );
}

InsightsDateSelector.propTypes = {
  dates: PropTypes.object,
  onDateChange: PropTypes.func,
};
