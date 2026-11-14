import { Box, Typography, styled, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { useEffect } from "react";
import Iconify from "src/components/iconify";
import { OutlinedButton } from "src/sections/project-detail/ProjectDetailComponents";
import {
  StyledIntervalSelect,
  StyledIntervalMenuItem,
} from "src/sections/projects/SharedComponents";

const AggregationButtonOptions = [
  { title: "Hour", value: "hour" },
  { title: "Day", value: "day" },
  { title: "Week", value: "week" },
  { title: "Month", value: "month" },
];

const CustomIconButton = styled(OutlinedButton)(({ theme }) => ({
  "& .MuiButton-startIcon": {
    margin: 0,
  },
  padding: "6px 6px",
  backgroundColor: theme.palette.paper,
  borderColor: theme.palette.divider,
  minWidth: 0,
  height: "unset",
}));

const RightControl = ({
  disabled,
  selectedInterval,
  setSelectedInterval,
  isMoreThan7Days,
  onZoomIn,
  onZoomOut,
  onMoveAhead,
  onMoveBack,
  isLessThan90Days,
}) => {
  useEffect(() => {
    if (isMoreThan7Days && selectedInterval === "hour") {
      setSelectedInterval("day");
    } else if (isLessThan90Days && selectedInterval === "month") {
      setSelectedInterval("week");
    }
    // causing extra renders if recommended deps added
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMoreThan7Days, isLessThan90Days]);

  const theme = useTheme();

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        opacity: disabled ? "0.5" : "1",
      }}
    >
      <StyledIntervalSelect
        value={
          AggregationButtonOptions.find((opt) => opt.value === selectedInterval)
            ?.title || selectedInterval
        }
        onChange={(e) => {
          const selectedOption = AggregationButtonOptions.find(
            (opt) => opt.title === e.target.value,
          );
          if (selectedOption) {
            setSelectedInterval(selectedOption.value);
          }
        }}
        size="small"
        disabled={disabled}
        MenuProps={{
          PaperProps: {
            sx: {
              borderRadius: theme.spacing(0.5),
              mt: 0.5,
            },
          },
        }}
      >
        {AggregationButtonOptions.map((option) => (
          <StyledIntervalMenuItem
            key={option.title}
            value={option.title}
            disabled={
              disabled ||
              (option.value === "hour" && isMoreThan7Days) ||
              (isLessThan90Days && option.title === "Month")
            }
          >
            <Typography variant="s2">{option.title}</Typography>
          </StyledIntervalMenuItem>
        ))}
      </StyledIntervalSelect>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: theme.spacing(1) }}
      >
        <CustomIconButton
          variant="outlined"
          startIcon={
            <Iconify
              icon="solar:alt-arrow-left-linear"
              sx={{ color: "text.primary" }}
            />
          }
          disabled={disabled}
          onClick={onMoveBack}
        />
        <CustomIconButton
          variant="outlined"
          startIcon={
            <Iconify
              icon="solar:alt-arrow-right-linear"
              sx={{ color: "text.primary" }}
            />
          }
          disabled={disabled}
          onClick={onMoveAhead}
        />
        <CustomIconButton
          variant="outlined"
          startIcon={
            <Iconify
              icon="solar:magnifier-zoom-in-linear"
              sx={{ color: "text.primary" }}
            />
          }
          disabled={disabled}
          onClick={onZoomIn}
        />
        <CustomIconButton
          variant="outlined"
          startIcon={
            <Iconify
              icon="solar:magnifier-zoom-out-linear"
              sx={{ color: "text.primary" }}
            />
          }
          disabled={disabled}
          onClick={onZoomOut}
        />
      </Box>
    </Box>
  );
};

RightControl.propTypes = {
  disabled: PropTypes.bool,
  selectedInterval: PropTypes.string,
  setSelectedInterval: PropTypes.func,
  index: PropTypes.number,
  isMoreThan7Days: PropTypes.bool,
  onZoomIn: PropTypes.func,
  onZoomOut: PropTypes.func,
  onMoveAhead: PropTypes.func,
  onMoveBack: PropTypes.func,
  isLessThan90Days: PropTypes.bool,
};

export default RightControl;
