import { Box, Button } from "@mui/material";
import React, { useEffect, useRef, useState } from "react";
import { formatDate } from "src/utils/report-utils";
import {
  endOfToday,
  format,
  startOfToday,
  startOfYesterday,
  startOfTomorrow,
  sub,
  differenceInMinutes,
  differenceInHours,
  differenceInDays,
  differenceInMonths,
  parseISO,
} from "date-fns";
import PropTypes from "prop-types";
import logger from "src/utils/logger";
import Iconify from "src/components/iconify";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
// Time period options exactly as shown in screenshot
const TIME_PERIOD_OPTIONS = [
  { title: "30 mins" },
  { title: "6 hrs" },
  { title: "Today" },
  { title: "Yesterday" },
  { title: "7D" },
  { title: "30D" },
  { title: "3M" },
  { title: "6M" },
  { title: "12M" },
];

const DateTimeRangePicker = ({
  setParentDateFilter,
  zoomRange = [null, null],
  dateOption,
  setDateOption,
  dateFilter: initialDateFilter,
  isEdit,
  includeOneHour = false,
}) => {
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);
  const dateDisplayRef = useRef(null);
  const lastPropagatedDateFilterRef = useRef(null);
  const [startDate, setStartDate] = useState(null);
  const [endDate, setEndDate] = useState(null);
  const [dateFilter, setDateFilter] = useState(() => {
    if (initialDateFilter && initialDateFilter[0] && initialDateFilter[1]) {
      return initialDateFilter;
    }

    return [
      formatDate(
        sub(new Date(), {
          days: 30,
        }),
      ),
      formatDate(endOfToday()),
    ];
  });

  // Function to detect the time period based on date filter
  const detectTimePeriod = (start, end) => {
    if (!start || !end) {
      return null;
    }

    try {
      // Parse the dates if they are strings (format: "2025-05-15 15:12:25")
      const startDate = typeof start === "string" ? parseISO(start) : start;
      const endDate = typeof end === "string" ? parseISO(end) : end;

      // Calculate the difference
      const minutesDiff = differenceInMinutes(endDate, startDate);
      const hoursDiff = differenceInHours(endDate, startDate);
      const daysDiff = differenceInDays(endDate, startDate);
      const monthsDiff = differenceInMonths(endDate, startDate);

      // Check if it matches any predefined option
      if (minutesDiff >= 25 && minutesDiff <= 35) {
        return "30 mins";
      } else if (includeOneHour && minutesDiff >= 55 && minutesDiff <= 65) {
        return "1 hr";
      } else if (hoursDiff >= 5.5 && hoursDiff <= 6.5) {
        return "6 hrs";
      } else if (
        format(startDate, "yyyy-MM-dd") ===
          format(startOfToday(), "yyyy-MM-dd") &&
        (format(endDate, "yyyy-MM-dd") ===
          format(startOfTomorrow(), "yyyy-MM-dd") ||
          format(endDate, "yyyy-MM-dd") === format(endOfToday(), "yyyy-MM-dd"))
      ) {
        return "Today";
      } else if (
        format(startDate, "yyyy-MM-dd") ===
          format(startOfYesterday(), "yyyy-MM-dd") &&
        format(endDate, "yyyy-MM-dd") === format(startOfToday(), "yyyy-MM-dd")
      ) {
        return "Yesterday";
      } else if (daysDiff >= 6 && daysDiff <= 8) {
        return "7D";
      } else if (daysDiff >= 29 && daysDiff <= 31) {
        return "30D";
      } else if (monthsDiff >= 2.8 && monthsDiff <= 3.2) {
        return "3M";
      } else if (monthsDiff >= 5.8 && monthsDiff <= 6.2) {
        return "6M";
      } else if (monthsDiff >= 11.8 && monthsDiff <= 12.2) {
        return "12M";
      }

      return "Custom";
    } catch (error) {
      logger.error("Error detecting time period:", error);
      return "Custom";
    }
  };

  const handleDataOptionChange = (newOption) => {
    let filter = null;
    setStartDate(null);
    setEndDate(null);

    switch (newOption) {
      case "Today":
        filter = [formatDate(startOfToday()), formatDate(startOfTomorrow())];
        break;
      case "Yesterday":
        filter = [formatDate(startOfYesterday()), formatDate(startOfToday())];
        break;
      case "7D":
        filter = [
          formatDate(
            sub(new Date(), {
              days: 7,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "30D":
        filter = [
          formatDate(
            sub(new Date(), {
              days: 30,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "3M":
        filter = [
          formatDate(
            sub(new Date(), {
              months: 3,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "6M":
        filter = [
          formatDate(
            sub(new Date(), {
              months: 6,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "12M":
        filter = [
          formatDate(
            sub(new Date(), {
              months: 12,
            }),
          ),
          formatDate(startOfTomorrow()),
        ];
        break;
      case "30 mins":
        filter = [
          formatDate(
            sub(new Date(), {
              minutes: 30,
            }),
          ),
          formatDate(new Date()),
        ];
        break;
      case "1 hr":
        filter = [
          formatDate(
            sub(new Date(), {
              hours: 1,
            }),
          ),
          formatDate(new Date()),
        ];
        break;
      case "6 hrs":
        filter = [
          formatDate(
            sub(new Date(), {
              hours: 6,
            }),
          ),
          formatDate(new Date()),
        ];
        break;
      default:
        break;
    }

    if (filter) {
      setDateFilter(filter);
      // Keep the parent option and range atomic. Waiting for the effect below
      // briefly pairs the new option with the previous range and can trigger
      // duplicate API reads against an incorrect window.
      lastPropagatedDateFilterRef.current = filter;
      setParentDateFilter?.(filter);
    }

    setDateOption(newOption);
  };
  const getButtonStyles = (selected, isFirst = false, isLast = false) => ({
    fontSize: "12px",
    fontWeight: selected ? 600 : 400,
    color: selected ? "primary.main" : "text.primary",
    backgroundColor: selected ? "action.hover" : "transparent",
    textTransform: "none",
    height: "28px",
    minWidth: 0,
    px: 1.5,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    borderRight: isLast ? "none" : "1px solid",
    borderColor: "divider",

    borderRadius: 0,
    borderLeft: isFirst ? "none" : "0.5px solid divider",
    // 👈 Collapse left border for all but first

    transition:
      "background-color 0.2s ease, font-weight 0.2s ease, color 0.2s ease, border-color 0.2s ease",
    "&:hover": {
      backgroundColor: selected ? "action.hover" : "transparent",
      borderColor: "divider",
    },
  });

  useEffect(() => {
    if (zoomRange && zoomRange.length === 2 && zoomRange[0] && zoomRange[1]) {
      const filter = [zoomRange[0], zoomRange[1]];
      setDateFilter(filter);
      lastPropagatedDateFilterRef.current = filter;
      setParentDateFilter?.(filter);
      setDateOption("Custom");
    }
  }, [zoomRange]);

  useEffect(() => {
    if (isEdit && dateFilter && dateFilter[0] && dateFilter[1]) {
      const detectedPeriod = detectTimePeriod(dateFilter[0], dateFilter[1]);
      if (detectedPeriod && detectedPeriod !== dateOption) {
        setDateOption(detectedPeriod);
      } else if (detectedPeriod === "Custom" && dateOption !== "Custom") {
        setDateOption("Custom");
      }
    }
  }, [dateFilter, isEdit]);

  useEffect(() => {
    if (
      setParentDateFilter &&
      lastPropagatedDateFilterRef.current !== dateFilter
    ) {
      lastPropagatedDateFilterRef.current = dateFilter;
      setParentDateFilter(dateFilter);
    }
    setStartDate(format(new Date(dateFilter[0]), "dd/MM/yyyy"));
    setEndDate(format(new Date(dateFilter[1]), "dd/MM/yyyy"));
  }, [dateOption, dateFilter]);

  return (
    <Box
      sx={{
        position: "relative",
        width: "100%",
        overflowX: "auto",
        "&::-webkit-scrollbar": {
          height: "4px",
        },
        "&::-webkit-scrollbar-track": {
          backgroundColor: "divider",
        },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: "divider",
          borderRadius: "4px",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "28px",
          color: "text.primary",
          border: "1px solid",
          borderColor: "divider",
          width: "fit-content",
          borderRadius: "4px",
          minWidth:
            (isEdit && dateOption === "Custom") || (startDate && endDate)
              ? "556px"
              : "427px",
          p: 0,
        }}
      >
        {/* Custom date button */}
        <Button
          ref={dateDisplayRef}
          onClick={() => setIsDatePickerOpen(true)}
          sx={{
            ...getButtonStyles(
              dateOption === "Custom",
              true, // isFirst
              false,
            ),
            display: "flex",
            alignItems: "center",
            gap: 1,
            justifyContent: "center",
          }}
          disableRipple
        >
          {dateOption === "Custom" && startDate && endDate ? (
            <Iconify icon="uil:calender" height={16} width={16} />
          ) : null}
          {startDate && endDate && dateOption === "Custom"
            ? `${startDate} - ${endDate}`
            : "Custom"}
        </Button>

        {/* Time period buttons */}
        {(includeOneHour
          ? [
              TIME_PERIOD_OPTIONS[0],
              { title: "1 hr" },
              ...TIME_PERIOD_OPTIONS.slice(1),
            ]
          : TIME_PERIOD_OPTIONS
        ).map((option, index, options) => {
          const selected = dateOption === option.title;
          const isLast = index === options.length - 1;

          return (
            <Button
              key={option.title}
              onClick={() => handleDataOptionChange(option.title)}
              sx={getButtonStyles(selected, false, isLast)}
              disableRipple
            >
              {option.title}
            </Button>
          );
        })}

        {/* Date Picker Popover */}
        <CustomDateRangePicker
          open={isDatePickerOpen}
          onClose={() => setIsDatePickerOpen(false)}
          anchorEl={dateDisplayRef?.current}
          setDateFilter={(filter) => {
            setDateFilter(filter);
            lastPropagatedDateFilterRef.current = filter;
            setParentDateFilter?.(filter);
          }}
          setDateOption={setDateOption}
        />
      </Box>
    </Box>
  );
};

DateTimeRangePicker.propTypes = {
  setParentDateFilter: PropTypes.func,
  zoomRange: PropTypes.array,
  dateOption: PropTypes.string,
  setDateOption: PropTypes.func,
  dateFilter: PropTypes.array,
  isEdit: PropTypes.bool,
  includeOneHour: PropTypes.bool,
};

DateTimeRangePicker.defaultProps = {
  setParentDateFilter: () => {},
  zoomRange: [null, null],
  dateOption: "30D",
  setDateOption: () => {},
};

export default DateTimeRangePicker;
