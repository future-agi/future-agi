import { Box, Button, ButtonGroup, useTheme } from "@mui/material";
import {
  format,
  startOfToday,
  startOfTomorrow,
  startOfYesterday,
  sub,
} from "date-fns";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { DateRangeButtonOptions } from "src/utils/constants";
import { formatDate } from "src/utils/report-utils";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";

const TracingControls = ({
  dateFilter,
  setDateFilter,
  observeId,
  fullWidth = true,
}) => {
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);

  const customDatePickerAnc = useRef(null);
  const theme = useTheme();

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

    setDateFilter((e) => ({
      dateFilter: filter ? filter : e.dateFilter,
      dateOption: newOption,
    }));
  };

  const getDateOptionTitle = (option, isSelected) => {
    if (option === "Custom" && isSelected) {
      return `${format(new Date(dateFilter.dateFilter[0]), "dd/MM/yyyy")} - ${format(new Date(dateFilter.dateFilter[1]), "dd/MM/yyyy")}`;
    }

    return option;
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        ...(fullWidth && { width: "100%" }),
      }}
    >
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
                  color={selected ? "primary" : "inherit"}
                  sx={{
                    flex: isCustomSelected ? "0 1 auto" : "1 0 auto", // Adjust flex behavior
                    backgroundColor: selected ? "action.hover" : undefined,
                    fontWeight: selected ? 600 : 400,

                    borderColor: "divider",
                    borderWidth: "1px",
                    px: 1.5, // Slightly more padding
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
                    height: "28px",
                    borderRadius: theme.spacing(0.5),
                  }}
                  ref={(ref) => {
                    if (isCustom) {
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
                    isCustomSelected ? (
                      <Iconify icon="uil:calender" height={20} width={20} />
                    ) : null
                  }
                >
                  <Box>{getDateOptionTitle(option.title, selected)}</Box>
                </Button>
              );
            })}
          </ButtonGroup>
        </Box>
        <CustomDateRangePicker
          open={isDatePickerOpen}
          onClose={() => setIsDatePickerOpen(false)}
          anchorEl={customDatePickerAnc?.current}
          value={dateFilter?.dateFilter}
          setDateFilter={(range) =>
            setDateFilter((prev) => ({
              ...prev,
              dateFilter: range,
              dateOption: "Custom",
            }))
          }
          setDateOption={() => {}}
        />
      </Box>
    </Box>
  );
};

TracingControls.propTypes = {
  dateFilter: PropTypes.shape({
    dateFilter: PropTypes.array,
    dateOption: PropTypes.string,
  }),
  setDateFilter: PropTypes.func,
  observeId: PropTypes.string,
  fullWidth: PropTypes.bool,
};

export default TracingControls;
