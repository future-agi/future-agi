import { Box, Typography } from "@mui/material";
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";

const colorMap = (val) => {
  if (val < 40) return "status.danger.fg";
  if (val < 70) return "status.warning.fg";
  return "status.success.fg";
};

const TestRunInsightCard = ({
  title,
  suffix,
  value,
  maxValue,
  highlight,
  secondaryText,
  tooltipTitle,
}) => {
  const color = useMemo(() => {
    if (highlight) return colorMap((value / maxValue) * 100);
    return "text.primary";
  }, [highlight, value, maxValue]);

  return (
    <CustomTooltip
      show={tooltipTitle}
      placement="bottom"
      title={tooltipTitle}
      arrow
      size="small"
    >
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          display: "inline-flex",
          padding: 2,
          backgroundColor: "background.paper",
          minWidth: "200px",
          flex: 1,
          flexDirection: "column",
          cursor: tooltipTitle ? "pointer" : "default",
        }}
      >
        <Box sx={{ display: "flex", gap: 1, alignItems: "baseline" }}>
          <Typography
            typography="m1"
            fontWeight={"fontWeightBold"}
            color={color}
          >
            {value === null ? "-" : `${value}${suffix || ""}`}
          </Typography>
          <ShowComponent condition={secondaryText}>
            <Typography typography="s3" fontWeight={"fontWeightMedium"}>
              {secondaryText}
            </Typography>
          </ShowComponent>
        </Box>
        <Typography
          typography="s1"
          color="text.disabled"
          fontWeight={"fontWeightMedium"}
        >
          {title}
        </Typography>
      </Box>
    </CustomTooltip>
  );
};

TestRunInsightCard.propTypes = {
  title: PropTypes.string,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  maxValue: PropTypes.number,
  suffix: PropTypes.string,
  percentage: PropTypes.bool,
  highlight: PropTypes.bool,
  secondaryText: PropTypes.string,
  tooltipTitle: PropTypes.string,
};

export default TestRunInsightCard;
