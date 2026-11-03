import React from "react";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "../../../components/svg-color/svg-color";
import CustomTooltip from "../../../components/tooltip/CustomTooltip";
import { fCurrency } from "src/utils/format-number";
import { ShowComponent } from "../../../components/show/ShowComponent";

export const TotalCellRenderer = (props) => {
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
      }}
    >
      <Typography
        typography={"s1"}
        fontWeight={"fontWeightMedium"}
        color={"text.primary"}
      >
        {props?.valueFormatted ?? props?.value}
      </Typography>
    </Box>
  );
};

TotalCellRenderer.propTypes = {
  valueFormatted: PropTypes.any,
  value: PropTypes.any,
  currentTab: PropTypes.oneOf(["cost", "count"]),
};

export const EvaluationCellRender = (props) => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
        flexDirection: "row",
        gap: 1,
        position: "relative",
      }}
    >
      <Typography
        typography={"s3"}
        fontWeight={"fontWeightRegular"}
        color={"text.primary"}
      >
        {props?.currentTab === "count"
          ? props?.value
          : fCurrency(!props?.value ? "0" : props.value)}
      </Typography>
      <ShowComponent condition={props?.data?.id}>
        <CustomTooltip show arrow title="View cost breakdown for evaluations">
          <IconButton
            size="small"
            sx={{
              padding: theme.spacing(0.25, 1.5),
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              position: "absolute",
              right: "-6px",
              "&:hover": {
                bgcolor: "background.paper",
              },
            }}
            onClick={() => {
              if (typeof props?.onViewBreakdown === "function") {
                props.onViewBreakdown(props?.data);
              }
            }}
          >
            <SvgColor
              sx={{
                height: 18,
                width: 18,
                color: "text.disabled",
              }}
              src="/assets/icons/custom/eye.svg"
            />
          </IconButton>
        </CustomTooltip>
      </ShowComponent>
    </Box>
  );
};

EvaluationCellRender.propTypes = {
  value: PropTypes.any,
  onViewBreakdown: PropTypes.func,
  data: PropTypes.any,
  currentTab: PropTypes.oneOf(["cost", "count"]),
};
