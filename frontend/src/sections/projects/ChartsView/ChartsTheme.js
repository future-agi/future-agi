import { memo } from "react";
import ApexChart from "react-apexcharts";
import { alpha, styled } from "@mui/material/styles";
import { bgBlur } from "src/theme/css";

// ----------------------------------------------------------------------

const Chart = styled(ApexChart)(({ theme }) => ({
  "& .apexcharts-canvas": {
    // Tooltip
    "& .apexcharts-tooltip": {
      ...bgBlur({
        color: theme.palette.background.default,
      }),
      color: theme.palette.text.primary,
      boxShadow: theme.customShadows.dropdown,
      borderRadius: theme.shape.borderRadius * 1.25,
      "&.apexcharts-theme-light": {
        borderColor: "transparent",
        ...bgBlur({
          color: theme.palette.background.paper,
        }),
      },
    },
    "& .apexcharts-xaxistooltip": {
      ...bgBlur({
        color: theme.palette.background.paper,
      }),
      borderColor: "transparent",
      color: theme.palette.text.primary,
      boxShadow: theme.customShadows.dropdown,
      borderRadius: theme.shape.borderRadius * 1.25,
      "&:before": {
        borderBottomColor: alpha(theme.palette.text.disabled, 0.24),
      },
      "&:after": {
        borderBottomColor: alpha(theme.palette.background.default, 0.8),
      },
    },
    "& .apexcharts-tooltip-title": {
      textAlign: "center",
      fontWeight: theme.typography.fontWeightSemiBold,
      backgroundColor: alpha(theme.palette.text.disabled, 0.08),
      color:
        theme.palette.text[
          theme.palette.mode === "light" ? "secondary" : "primary"
        ],
    },
    // apexcharts-legend-marker
    // LEGEND
    "& .apexcharts-legend": {
      padding: 0,
    },
    "& .apexcharts-legend-series": {
      display: "inline-flex !important",
      alignItems: "center",
    },
    "& .apexcharts-legend-marker": {
      marginRight: 8,
      // borderRadius: "0px !important",
    },
    "& .apexcharts-legend-text": {
      lineHeight: "18px",
      textTransform: "capitalize",
    },
  },
}));

export default memo(Chart);
