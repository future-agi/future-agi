import React from "react";
import { buttonClasses } from "@mui/material/Button";
import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------

const dateList = [
  "DatePicker",
  "DateTimePicker",
  "StaticDatePicker",
  "DesktopDatePicker",
  "DesktopDateTimePicker",
  //
  "MobileDatePicker",
  "MobileDateTimePicker",
];

const timeList = [
  "TimePicker",
  "MobileTimePicker",
  "StaticTimePicker",
  "DesktopTimePicker",
];

const switchIcon = () => (
  <Iconify icon="solar:alt-arrow-down-linear" width={24} />
);

const leftIcon = () => (
  <Iconify icon="solar:alt-arrow-left-linear" width={24} />
);

const rightIcon = () => (
  <Iconify icon="solar:alt-arrow-right-linear" width={24} />
);

const calendarIcon = () => (
  <Iconify icon="solar:calendar-mark-bold-duotone" width={24} />
);

const clockIcon = () => (
  <Iconify icon="solar:clock-circle-outline" width={24} />
);

const desktopTypes = dateList.reduce((result, currentValue) => {
  result[`Mui${currentValue}`] = {
    defaultProps: {
      slots: {
        openPickerIcon: calendarIcon,
        leftArrowIcon: leftIcon,
        rightArrowIcon: rightIcon,
        switchViewIcon: switchIcon,
      },
    },
  };

  return result;
}, {});

const timeTypes = timeList.reduce((result, currentValue) => {
  result[`Mui${currentValue}`] = {
    defaultProps: {
      slots: {
        openPickerIcon: clockIcon,
        rightArrowIcon: rightIcon,
        switchViewIcon: switchIcon,
      },
    },
  };

  return result;
}, {});

export function datePicker(theme) {
  return {
    MuiPickersLayout: {
      styleOverrides: {
        root: {
          "& .MuiPickersLayout-actionBar": {
            [`& .${buttonClasses.root}:last-of-type`]: {
              backgroundColor: theme.palette.text.primary,
              color:
                theme.palette.mode === "light"
                  ? theme.palette.common.white
                  : theme.palette.grey[800],
            },
          },
        },
      },
    },

    // Date
    ...desktopTypes,

    // Time
    ...timeTypes,
  };
}
