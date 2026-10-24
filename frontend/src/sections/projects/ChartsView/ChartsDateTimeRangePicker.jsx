import { Box, Button, ButtonGroup, Typography } from "@mui/material";
import React, { useEffect, useRef, useState } from "react";
import { DateRangeButtonOptions } from "src/utils/constants";
import { formatDate } from "src/utils/report-utils";
import {
  endOfToday,
  format,
  startOfToday,
  startOfYesterday,
  startOfTomorrow,
  sub,
} from "date-fns";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";

const ChartsDateTimeRangePicker = ({
  setParentDateFilter,
  zoomRange = [null, null],
  observeId,
  dateOption,
  setDateOption,
}) => {
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);

  const customDatePickerAnc = useRef(null);

  const [dateFilter, setDateFilter] = useState(() => {
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
      default:
        break;
    }
    if (filter) {
      setDateFilter(filter);
    }

    setDateOption(newOption);
  };

  useEffect(() => {
    if (zoomRange && zoomRange.length === 2 && zoomRange[0] && zoomRange[1]) {
      setDateFilter([zoomRange[0], zoomRange[1]]);
      setDateOption("Custom");
    }
  }, [zoomRange]);

  const getDateOptionTitle = (option, isSelected) => {
    if (option === "Custom" && isSelected) {
      return `${format(new Date(dateFilter[0]), "dd/MM/yyyy")} - ${format(new Date(dateFilter[1]), "dd/MM/yyyy")}`;
    }

    return option;
  };

  useEffect(() => {
    if (setParentDateFilter) {
      setParentDateFilter(dateFilter);
    }
  }, [dateOption, dateFilter]);

  return (
    <Box
      sx={{
        display: "flex",
        gap: 1,
        alignItems: "center",
        width: "100%",
        overflowX: "auto",
        // marginTop: theme.spacing(0.5),
      }}
    >
      <ButtonGroup variant="outlined" color="inherit" size="small">
        {DateRangeButtonOptions.map((option) => {
          const selected = dateOption === option.title;
          const isCustom = option.title === "Custom";
          return (
            <Button
              size="small"
              color={selected ? "primary" : "inherit"}
              sx={{
                flex: isCustom ? "0 1 auto" : "1 0 auto", // Adjust flex behavior
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
                if (option.title === "Custom") {
                  customDatePickerAnc.current = ref;
                }
              }}
              onClick={() => {
                handleDataOptionChange(option.title);
                trackEvent(Events.observeDatechangeClicked, {
                  [PropertyName.id]: observeId,
                });
              }}
              key={option.title}
              startIcon={
                isCustom && selected ? (
                  <Iconify
                    icon="solar:calendar-linear"
                    height={20}
                    width={20}
                  />
                ) : null
              }
            >
              <Typography variant="s2">
                {getDateOptionTitle(option.title, selected, dateFilter)}
              </Typography>
            </Button>
          );
        })}
      </ButtonGroup>
      <CustomDateRangePicker
        open={isDatePickerOpen}
        onClose={() => setIsDatePickerOpen(false)}
        anchorEl={customDatePickerAnc?.current}
        setDateFilter={setDateFilter}
        setDateOption={setDateOption}
      />
    </Box>
  );
};

ChartsDateTimeRangePicker.propTypes = {
  setParentDateFilter: PropTypes.func,
  zoomRange: PropTypes.array,
  observeId: PropTypes.string,
  dateOption: PropTypes.string,
  setDateOption: PropTypes.func,
};

ChartsDateTimeRangePicker.defaultProps = {
  setParentDateFilter: () => {},
  zoomRange: [null, null],
};

export default ChartsDateTimeRangePicker;
