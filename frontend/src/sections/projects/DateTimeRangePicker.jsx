import { Box, Button } from "@mui/material";
import React, { useEffect, useRef, useState } from "react";
import { formatDate } from "src/utils/report-utils";
import { endOfToday, format, sub } from "date-fns";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
import { TIME_PERIOD_OPTIONS, presetToRange } from "./timeWindowPresets";
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

  const handleDataOptionChange = (newOption) => {
    setStartDate(null);
    setEndDate(null);
    const range = presetToRange(newOption);
    if (range) {
      const filter = [formatDate(range[0]), formatDate(range[1])];
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
          value={dateFilter}
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
