import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Button, MenuItem, Popover } from "@mui/material";
import { startOfToday, startOfTomorrow, startOfYesterday, sub } from "date-fns";
import Iconify from "src/components/iconify";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
import { formatDate } from "src/utils/report-utils";

export const DATE_OPTIONS = [
  { key: "Today", label: "Today" },
  { key: "Yesterday", label: "Yesterday" },
  { key: "7D", label: "Past 7D" },
  { key: "30D", label: "Past 30D" },
  { key: "3M", label: "Past 3M" },
  { key: "6M", label: "Past 6M" },
  { key: "12M", label: "Past 12M" },
  { key: "Custom", label: "Custom range" },
];

export const DATE_OPTION_LABELS = DATE_OPTIONS.reduce((acc, o) => {
  acc[o.key] = o.label;
  return acc;
}, {});

const DEFAULT_PILL_SX = {
  textTransform: "none",
  fontWeight: 500,
  fontSize: 13,
  height: 32,
  borderColor: "divider",
  color: "text.primary",
  "&:hover": { borderColor: "text.secondary" },
};

export function dateFilterForOption(option) {
  switch (option) {
    case "Today":
      return [formatDate(startOfToday()), formatDate(startOfTomorrow())];
    case "Yesterday":
      return [formatDate(startOfYesterday()), formatDate(startOfToday())];
    case "7D":
      return [
        formatDate(sub(new Date(), { days: 7 })),
        formatDate(startOfTomorrow()),
      ];
    case "30D":
      return [
        formatDate(sub(new Date(), { days: 30 })),
        formatDate(startOfTomorrow()),
      ];
    case "3M":
      return [
        formatDate(sub(new Date(), { months: 3 })),
        formatDate(startOfTomorrow()),
      ];
    case "6M":
      return [
        formatDate(sub(new Date(), { months: 6 })),
        formatDate(startOfTomorrow()),
      ];
    case "12M":
      return [
        formatDate(sub(new Date(), { months: 12 })),
        formatDate(startOfTomorrow()),
      ];
    default:
      return null;
  }
}

const DateRangePill = ({ dateFilter, setDateFilter, label, sx }) => {
  const [dateAnchor, setDateAnchor] = useState(null);
  const [customDateOpen, setCustomDateOpen] = useState(false);
  const dateButtonRef = useRef(null);

  const displayLabel =
    label ||
    (dateFilter?.dateOption
      ? DATE_OPTION_LABELS[dateFilter.dateOption] || "Custom"
      : "Past 7D");

  const handleDateOptionChange = (option) => {
    setDateAnchor(null);
    if (!setDateFilter) return;
    if (option === "Custom") {
      setCustomDateOpen(true);
      return;
    }
    const filter = dateFilterForOption(option);
    if (filter) {
      setDateFilter((prev) => ({
        ...prev,
        dateFilter: filter,
        dateOption: option,
      }));
    }
  };

  return (
    <>
      <Button
        ref={dateButtonRef}
        variant="outlined"
        size="small"
        startIcon={<Iconify icon="mdi:calendar-outline" width={16} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onClick={(e) => setDateAnchor(e.currentTarget)}
        sx={{ ...DEFAULT_PILL_SX, ...sx }}
      >
        {displayLabel}
      </Button>
      <Popover
        open={Boolean(dateAnchor)}
        anchorEl={dateAnchor}
        onClose={() => setDateAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: { sx: { mt: 0.5, borderRadius: "8px", minWidth: 140 } },
        }}
      >
        {DATE_OPTIONS.map((opt) => (
          <MenuItem
            key={opt.key}
            selected={dateFilter?.dateOption === opt.key}
            onClick={() => handleDateOptionChange(opt.key)}
            sx={{ fontSize: 13, py: 0.75 }}
          >
            {opt.label}
          </MenuItem>
        ))}
      </Popover>
      <CustomDateRangePicker
        open={customDateOpen}
        onClose={() => setCustomDateOpen(false)}
        anchorEl={dateButtonRef.current}
        setDateFilter={(range) => {
          setDateFilter?.((prev) => ({
            ...prev,
            dateFilter: range,
            dateOption: "Custom",
          }));
          setCustomDateOpen(false);
        }}
        setDateOption={() => {}}
      />
    </>
  );
};

DateRangePill.propTypes = {
  dateFilter: PropTypes.object,
  setDateFilter: PropTypes.func.isRequired,
  label: PropTypes.string,
  sx: PropTypes.object,
};

export default DateRangePill;
