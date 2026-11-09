import { Box, Button, ButtonGroup } from "@mui/material";
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
import EvaluationDatePicker from "src/pages/dashboard/models/Performance/EvaluationDatePicker";

const EvaluationDateTimeRangePicker = ({
  setParentDateFilter,
  zoomRange = [null, null],
}) => {
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);

  const customDatePickerAnc = useRef(null);
  const [dateOption, setDateOption] = useState("30D");

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
    if (option === "Custom" && isSelected)
      return `${format(new Date(dateFilter[0]), "dd/MM/yyyy")} - ${format(new Date(dateFilter[1]), "dd/MM/yyyy")}`;

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
      }}
    >
      <ButtonGroup
        variant="outlined"
        color="inherit"
        size="small"
        sx={{ maxWidth: "100%" }}
      >
        {DateRangeButtonOptions.map((option, _) => {
          const selected = dateOption === option.title;
          return (
            <Button
              color={selected ? "primary" : "inherit"}
              sx={{
                // Adjust flex behavior
                backgroundColor: selected ? "action.hover" : undefined,

                borderColor: "divider",
                borderWidth: "1px",
                minWidth: 0,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                bgcolor: selected ? "action.hover" : undefined,
                "&:hover": {
                  bgcolor: selected ? "action.hover" : "transparent",
                  borderColor: "divider",
                  transition:
                    "background-color 160ms ease, color 160ms ease, border-color 160ms ease",
                },
              }}
              ref={(ref) => {
                if (option.title === "Custom")
                  customDatePickerAnc.current = ref;
              }}
              onClick={() => handleDataOptionChange(option.title)}
              key={option.title}
            >
              {getDateOptionTitle(option.title, selected, dateFilter)}
            </Button>
          );
        })}
      </ButtonGroup>
      <EvaluationDatePicker
        open={isDatePickerOpen}
        onClose={() => setIsDatePickerOpen(false)}
        anchorEl={customDatePickerAnc?.current}
        setDateFilter={setDateFilter}
        setDateOption={setDateOption}
      />
    </Box>
  );
};

EvaluationDateTimeRangePicker.propTypes = {
  setParentDateFilter: PropTypes.func,
  zoomRange: PropTypes.array,
};

EvaluationDateTimeRangePicker.defaultProps = {
  setParentDateFilter: () => {},
  zoomRange: [null, null],
};

export default EvaluationDateTimeRangePicker;
