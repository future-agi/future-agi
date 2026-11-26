import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Growth from "../Growth";
import { ShowComponent } from "../../../../../components/show";
import SvgColor from "../../../../../components/svg-color";

const TrialCellRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        height: "100%",
        lineHeight: "22px",
      }}
    >
      <Typography typography="s1">{value?.title ?? "-"}</Typography>
      <ShowComponent
        condition={
          value?.improvement !== null && value?.improvement !== undefined
        }
      >
        <Growth value={value?.improvement} />
      </ShowComponent>
      <ShowComponent condition={value?.isBest}>
        <Box
          sx={{
            padding: 0.5,
            backgroundColor: "status.warning.bg",
            borderRadius: "4px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor
            src="/icons/datasets/choose_winner.svg"
            sx={{ width: "18px", height: "18px" }}
            color="status.warning.fg"
          />
        </Box>
      </ShowComponent>
    </Box>
  );
};

TrialCellRenderer.propTypes = {
  value: PropTypes.object,
};

export default TrialCellRenderer;
