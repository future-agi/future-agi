import React, { useEffect, useRef, useState } from "react";
import { Box, Button, ButtonGroup } from "@mui/material";
import { formatDate } from "src/utils/report-utils";
import {
  format,
  startOfToday,
  startOfYesterday,
  startOfTomorrow,
  sub,
  differenceInDays,
} from "date-fns";
import PropTypes from "prop-types";
// import { DateRangeButtonOptions } from "src/utils/constants";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import { DateRangeButtonOptions, detectTimePeriod } from "../common";
import { useUrlState } from "src/routes/hooks/use-url-state";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";

const UsersDetailDateTimePicker = ({
  dateFilter,
  setDateFilter,
  setDateInterval,
  zoomRange = [null, null],
  setZoomRange,
  isEdit,
}) => {
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);
  const customDatePickerAnc = useRef(null);
  const [selectedProjectId] = useUrlState("projectId", null);

  const handleDataOptionChange = (newOption) => {
    let filter = null;
    switch (newOption) {
      case "Custom":
        setIsDatePickerOpen(true);
        return;
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
      setDateFilter(() => ({
        dateFilter: filter,
        dateOption: newOption,
      }));

      const diffInDays = differenceInDays(
        new Date(filter[1]),
        new Date(filter[0]),
      );
      setDateInterval(diffInDays > 7 ? "day" : "hour");
    }
    if (setZoomRange && newOption !== "Custom") {
      setZoomRange([null, null]);
    }
  };

  const getDateOptionTitle = (option, isSelected) => {
    if (option === "Custom" && isSelected) {
      return `${format(new Date(dateFilter.dateFilter[0]), "dd/MM/yyyy")} - ${format(new Date(dateFilter.dateFilter[1]), "dd/MM/yyyy")}`;
    }

    return option;
  };

  // Handle zoom range updates
  useEffect(() => {
    if (zoomRange && zoomRange.length === 2 && zoomRange[0] && zoomRange[1]) {
      setDateFilter((prev) => ({
        ...prev,
        dateFilter: [zoomRange[0], zoomRange[1]],
        dateOption: "Custom",
      }));
      const diffInDays = differenceInDays(
        new Date(zoomRange[1]),
        new Date(zoomRange[0]),
      );
      setDateInterval(diffInDays > 7 ? "day" : "hour");
    }
  }, [zoomRange, setDateFilter]);

  // Auto-detect period when in edit mode
  useEffect(() => {
    if (
      isEdit &&
      dateFilter?.dateFilter &&
      dateFilter.dateFilter[0] &&
      dateFilter.dateFilter[1]
    ) {
      const detectedPeriod = detectTimePeriod(
        dateFilter.dateFilter[0],
        dateFilter.dateFilter[1],
      );
      if (detectedPeriod && detectedPeriod !== dateFilter.dateOption) {
        setDateFilter((prev) => ({
          ...prev,
          dateOption: detectedPeriod,
        }));
      } else if (
        detectedPeriod === "Custom" &&
        dateFilter.dateOption !== "Custom"
      ) {
        setDateFilter((prev) => ({
          ...prev,
          dateOption: "Custom",
        }));
      }
    }
  }, [dateFilter?.dateFilter, isEdit, setDateFilter]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", width: "100%" }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flex: 1,
            alignItems: "center",
            minWidth: 0, // Crucial for text overflow
            overflow: "hidden", // Contain children
          }}
        >
          <ButtonGroup
            variant="outlined"
            color="inherit"
            size="small"
            sx={{
              display: "flex",
              width: "100%",
              minWidth: 0, // Important for text overflow
            }}
          >
            {DateRangeButtonOptions.map((option) => {
              const selected = dateFilter.dateOption === option.title;
              const isCustom = option.title === "Custom";
              const isCustomSelected = isCustom && selected;

              return (
                <Button
                  size="small"
                  color={selected ? "primary" : "inherit"}
                  sx={{
                    flex: isCustomSelected ? "0 1 auto" : "1 0 auto", // Adjust flex behavior
                    backgroundColor: selected ? "action.hover" : undefined,

                    borderColor: "divider",
                    borderWidth: "1px", // Slightly more padding
                    minWidth: 0,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    bgcolor: selected ? "action.hover" : undefined,
                    "&:hover": {
                      bgcolor: selected ? "action.hover" : "transparent",
                      borderColor: "divider",
                      transition:
                        "background-color 0.2s ease, font-weight 0.2s ease, color 0.2s ease, border-color 0.2s ease",
                    },
                  }}
                  ref={(ref) => {
                    if (isCustom) {
                      customDatePickerAnc.current = ref;
                    }
                  }}
                  onClick={() => {
                    handleDataOptionChange(option.title);
                    if (selectedProjectId) {
                      trackEvent(Events.observeDatechangeClicked, {
                        [PropertyName.id]: selectedProjectId,
                      });
                    }
                  }}
                  key={option.title}
                  startIcon={
                    isCustomSelected ? (
                      <Iconify
                        icon="solar:calendar-linear"
                        height={20}
                        width={20}
                      />
                    ) : null
                  }
                >
                  <Box
                    component="span"
                    sx={{
                      display: "inline-block",
                      maxWidth: "100%",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {getDateOptionTitle(option.title, selected)}
                  </Box>
                </Button>
              );
            })}
          </ButtonGroup>
        </Box>
        <CustomDateRangePicker
          open={isDatePickerOpen}
          onClose={() => setIsDatePickerOpen(false)}
          anchorEl={customDatePickerAnc?.current}
          setDateFilter={(range) => {
            setDateFilter((prev) => ({
              ...prev,
              dateFilter: range,
              dateOption: "Custom",
            }));

            const diffInDays = differenceInDays(
              new Date(range[1]),
              new Date(range[0]),
            );
            setDateInterval(diffInDays > 7 ? "day" : "hour");
          }}
          setDateOption={() => {}}
        />
      </Box>
    </Box>
  );
};

UsersDetailDateTimePicker.propTypes = {
  dateFilter: PropTypes.object,
  setDateInterval: PropTypes.func,
  isEdit: PropTypes.bool,
  zoomRange: PropTypes.array,
  setZoomRange: PropTypes.func,
  setDateFilter: PropTypes.func,
};

export default UsersDetailDateTimePicker;
