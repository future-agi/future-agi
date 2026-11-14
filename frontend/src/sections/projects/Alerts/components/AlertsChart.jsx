import { Box, IconButton, Typography, useTheme } from "@mui/material";
import React, { useCallback, useEffect, useRef, useState } from "react";
import ReactApexChart from "react-apexcharts";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";

export default function AlertsChart({ series, options }) {
  const [height, setHeight] = useState(300);
  const [isDragging, setIsDragging] = useState(false);
  const boxRef = useRef(null);
  const theme = useTheme();

  const handleMouseMove = useCallback(
    (e) => {
      if (isDragging) {
        const rect = boxRef.current.getBoundingClientRect();
        let newHeight = e.clientY - rect.y;
        newHeight = Math.max(0, newHeight);
        newHeight = Math.round(newHeight);
        if (newHeight > 300) return;
        setHeight(newHeight);
      }
    },
    [isDragging],
  );

  const handleMouseDown = () => {
    setIsDragging(true);
  };

  const toggleHeight = () => {
    setHeight(height > 0 ? 0 : 300);
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  useEffect(() => {
    if (isDragging) {
      window.addEventListener("mousemove", handleMouseMove);
      window.addEventListener("mouseup", handleMouseUp);
    } else {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    }

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [handleMouseMove, isDragging]);

  return (
    <Box
      sx={{
        height: `${height}px`,
        position: "relative",
        transition: isDragging ? "none" : "height 400ms ease-in-out",
      }}
      onMouseMove={handleMouseMove}
      ref={boxRef}
      className="alert-chart-wrapper"
    >
      <ReactApexChart
        options={options}
        series={series}
        type="line"
        height={300}
      />

      <Box
        display="flex"
        flexDirection="column"
        gap={1}
        sx={{
          position: "absolute",
          top: 2,
          right: 0,
          flexDirection: "row",
          display: height === 0 ? "none" : "flex",
        }}
      >
        <Box display="flex" alignItems="center" gap={1}>
          <SvgColor
            src="/assets/icons/status/success.svg"
            sx={{
              height: 16,
              width: 16,
              bgcolor: "status.success.solid",
            }}
          />
          <Typography
            variant="s3"
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            Healthy
          </Typography>
        </Box>
        <Box display="flex" alignItems="center" gap={1}>
          <SvgColor
            src="/assets/icons/ic_critical.svg"
            sx={{
              height: 16,
              width: 16,
              bgcolor: "status.danger.solid",
            }}
          />
          <Typography
            variant="s3"
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            Critical
          </Typography>
        </Box>
        <Box display="flex" alignItems="center" gap={1}>
          <SvgColor
            src="/assets/icons/ic_warning.svg"
            sx={{
              height: 16,
              width: 16,
              bgcolor: "status.warning.solid",
            }}
          />
          <Typography
            variant="s3"
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            Warning
          </Typography>
        </Box>
      </Box>

      <IconButton
        size="small"
        aria-label="Toggle chart height"
        sx={{
          position: "absolute",
          bottom: "-12px",
          left: "30px",
          zIndex: 10,
          border: "1px solid",
          borderColor: "divider",
          backgroundColor: "background.paper",
        }}
        onClick={toggleHeight}
      >
        <Iconify
          icon="solar:chevrons-down-up-linear"
          width={16}
          sx={{ color: "text.disabled" }}
        />
      </IconButton>
      <Box
        sx={{
          position: "absolute",
          bottom: "-12px",
          left: "70px",
          borderRadius: "50%",
          zIndex: 10,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          border: "1px solid",
          borderColor: "divider",
          padding: 0.3,
          backgroundColor: isDragging
            ? "background.neutral"
            : "background.paper",
          cursor: isDragging ? "grabbing" : "grab",
        }}
        onMouseDown={handleMouseDown}
      >
        <Iconify
          icon="solar:grip-horizontal-linear"
          width={16}
          sx={{ color: "text.disabled", padding: theme.spacing(0.25) }}
        />
      </Box>
    </Box>
  );
}

AlertsChart.propTypes = {
  options: PropTypes.object,
  series: PropTypes.array,
};
