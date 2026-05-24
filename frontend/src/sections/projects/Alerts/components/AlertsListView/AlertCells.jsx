import {
  Box,
  Button,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { format, differenceInDays, parseISO } from "date-fns";
import SvgColor from "src/components/svg-color";
import _ from "lodash";
import { useEffect, useRef } from "react";
import ApexCharts from "apexcharts";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { isAlertMuted } from "../../common";

export const IssueCell = ({ data }) => {
  if (!data) return null;

  const dateString = data?.updated_at || data?.created_at;

  let formattedDate = dateString;
  try {
    const parsedDate = parseISO(dateString);
    formattedDate = format(parsedDate, "dd-MM-yyyy, HH:mm");
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error("Date parsing failed:", e);
  }

  return (
    <Stack
      spacing={0.5}
      sx={{
        display: "flex",
        height: "100%",
        justifyContent: "center",
      }}
    >
      <Typography
        variant="s1"
        color={"text.primary"}
        fontWeight={"fontWeightMedium"}
      >
        {data?.name}
        <ShowComponent condition={isAlertMuted(data)}>
          <Typography
            component={"span"}
            sx={{
              ml: 1,
            }}
          >
            <SvgColor
              src="/assets/icons/ic_mute.svg"
              sx={{
                height: 16,
                width: 16,
                bgcolor: "red.500",
              }}
            />
          </Typography>
        </ShowComponent>
      </Typography>
      <Typography
        variant="s3"
        color="text.disabled"
        fontWeight={"fontWeightRegular"}
      >
        {data?.updated_at
          ? `Updated at ${formattedDate}`
          : `Created at ${formattedDate}`}
      </Typography>
    </Stack>
  );
};

IssueCell.propTypes = {
  data: PropTypes.shape({
    created_at: PropTypes.string,
    updated_at: PropTypes.string,
    name: PropTypes.string.isRequired,
    is_mute: PropTypes.bool,
    isMute: PropTypes.bool,
  }),
};

const statusCellStyle = {
  triggered: {
    sx: {
      color: "red.500",
      backgroundColor: "red.o10",
    },
    icon: "/assets/icons/ic_critical.svg",
  },
  healthy: {
    sx: {
      color: "green.500",
      backgroundColor: "green.o10",
    },
    icon: "/assets/icons/status/success.svg",
  },
  critical: {
    sx: {
      color: "red.500",
      backgroundColor: "red.o10",
    },
    icon: "/assets/icons/ic_critical.svg",
  },
  warning: {
    sx: {
      color: "orange.400",
      backgroundColor: "orange.o5",
    },
    icon: "/assets/icons/ic_warning.svg",
  },
  resolved: {
    sx: {
      color: "green.500",
      backgroundColor: "green.o10",
    },
    icon: "/assets/icons/status/success.svg",
  },
};

export const StatusCell = ({ value }) => {
  const theme = useTheme();
  if (!value) return "-";
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
      }}
    >
      <Stack
        sx={{
          ...statusCellStyle?.[_.toLower(value)]?.sx,
          padding: theme.spacing(0.5, 1.5),
          display: "flex",
          flexDirection: "row",
          justifyContent: "flex-start",
          alignItems: "center",
          height: "max-content",
          borderRadius: theme.spacing(0.5),
        }}
        gap={1}
      >
        <SvgColor
          src={statusCellStyle?.[_.toLower(value)]?.icon}
          sx={{
            height: 16,
            width: 16,
          }}
        />
        <Typography variant="s3" fontWeight={"fontWeightMedium"}>
          {_.capitalize(value)}
        </Typography>
      </Stack>
    </Box>
  );
};

StatusCell.propTypes = {
  value: PropTypes.string,
};

export const LastTriggeredCell = ({ value }) => {
  if (!value || value === "-" || value === null) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography>-</Typography>
      </Box>
    );
  }

  let daysAgo = "";
  let parsedDate;
  try {
    parsedDate = parseISO(value); // Correct for ISO strings
    const diffDays = differenceInDays(new Date(), parsedDate);
    daysAgo =
      diffDays === 0
        ? "Today"
        : `${diffDays} day${diffDays > 1 ? "s" : ""} ago`;

    parsedDate = format(parsedDate, "dd-MM-yyyy, HH:mm");
  } catch (err) {
    daysAgo = "-";
    parsedDate = "Invalid Date";
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        height: "100%",
      }}
    >
      <Typography
        variant="s1"
        color={"text.primary"}
        fontWeight={"fontWeightRegular"}
      >
        {daysAgo}
      </Typography>
      <Typography
        variant="s3"
        color={"text.disabled"}
        fontWeight={"fontWeightRegular"}
      >
        {parsedDate}
      </Typography>
    </Box>
  );
};

LastTriggeredCell.propTypes = {
  value: PropTypes.string,
};

export const TrendChartCell = ({ value }) => {
  const chartRef = useRef(null);

  useEffect(() => {
    if (!value || !Array.isArray(value) || value.length === 0) return;
    if (!chartRef.current) return;

    const chartOptions = {
      chart: {
        type: "line",
        height: 40,
        width: "100%",
        sparkline: { enabled: true },
      },
      stroke: { curve: "smooth", width: 2 },
      colors: ["#CF6BE8"],
      series: [{ data: value.map((item) => item.value) }],
      tooltip: { enabled: false },
      xaxis: {
        labels: { show: false },
        axisTicks: { show: false },
        axisBorder: { show: false },
      },
      yaxis: { show: false },
    };

    const chart = new ApexCharts(chartRef.current, chartOptions);
    chart.render();

    return () => chart.destroy();
  }, [value]);

  // Render nothing if no chart data
  if (!value || !Array.isArray(value) || value.length === 0) {
    return "-";
  }

  return (
    <div
      ref={chartRef}
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    />
  );
};

TrendChartCell.propTypes = {
  value: PropTypes.arrayOf(
    PropTypes.shape({
      timestamp: PropTypes.string.isRequired,
      value: PropTypes.number.isRequired,
    }),
  ),
};

export const ActionCell = ({ options = [], onClick }) => {
  const theme = useTheme();
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);

  const handleOpen = (e) => setAnchorEl(e.currentTarget);
  const handleClose = () => setAnchorEl(null);

  const handleItemClick = (option) => {
    if (onClick) onClick(option);
    handleClose();
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Button
        variant="outlined"
        size="small"
        onClick={handleOpen}
        sx={{
          display: "flex",
          gap: 1,
          borderRadius: theme.spacing(1),
          borderColor: `${theme.palette.divider} !important`,
          minWidth: 71,
          px: 1,
          py: 0.25,
        }}
      >
        <SvgColor
          sx={{
            bgcolor: "text.disabled",
            height: 16.5,
            width: 16.5,
          }}
          src="/assets/icons/action_buttons/ic_configure.svg"
        />
        <SvgColor
          sx={{
            bgcolor: "text.disabled",
            height: 16.5,
            width: 16.5,
            transform: "rotate(90deg)",
          }}
          src="/assets/icons/custom/lucide--chevron-right.svg"
        />
      </Button>

      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        anchorOrigin={{ horizontal: "right", vertical: "bottom" }}
        transformOrigin={{ horizontal: "right", vertical: "top" }}
        PaperProps={{
          elevation: 3,
          sx: {
            borderRadius: 1,
            px: theme.spacing(1.5),
            minWidth: 160,
          },
        }}
      >
        {options.map((option) => (
          <MenuItem
            key={option.value}
            onClick={() => handleItemClick(option?.value)}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              transition: "all 0.2s ease-in-out",
              "& .action-label": {
                transition: "all 0.2s ease-in-out",
              },
              "&:hover, &:active": {
                fontWeight: "fontWeightMedium",
                "& .action-label": {
                  fontWeight: "fontWeightMedium",
                },
              },
            }}
          >
            {option.component ? (
              option.component
            ) : (
              <Typography variant="body2">{option.label}</Typography>
            )}
          </MenuItem>
        ))}
      </Menu>
    </Box>
  );
};

ActionCell.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string,
      value: PropTypes.string.isRequired,
      component: PropTypes.node,
    }),
  ),
  onClick: PropTypes.func,
};

export const IssueNameCell = ({ value }) => {
  return (
    <CustomTooltip
      show
      title={value}
      arrow
      placement="bottom-start"
      slotProps={{
        popper: {
          modifiers: [
            {
              // Custom modifier to move arrow to start
              name: "arrow-start-align",
              enabled: true,
              phase: "write",
              fn({ state }) {
                if (state.placement.startsWith("bottom")) {
                  const arrowEl = state.elements.arrow;
                  if (arrowEl) {
                    arrowEl.style.left = "40px";
                    arrowEl.style.transform = "translateX(0)";
                  }
                }
              },
            },
          ],
        },
      }}
    >
      <Typography
        variant="s1"
        color="text.primary"
        fontWeight="fontWeightRegular"
      >
        {value}
      </Typography>
    </CustomTooltip>
  );
};

IssueNameCell.propTypes = {
  value: PropTypes.string,
};
