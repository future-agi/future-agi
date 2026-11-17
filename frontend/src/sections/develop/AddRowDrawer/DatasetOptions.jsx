import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";

/**
 * !IMPORTANT
 * This component is reused in multiple places like
 * - AddRowDrawer
 * - AddRowDrawer in simulate
 * if you make any change here please make sure you test in both places
 */

const DatasetOptions = ({ title, subTitle, onClick, icons }) => {
  return (
    <Box
      sx={{
        padding: 2,
        gap: 1,
        display: "flex",
        flexDirection: "column",
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        cursor: "pointer",
        "&:hover": {
          backgroundColor: "background.neutral",
        },
      }}
      onClick={onClick}
    >
      <Box sx={{ display: "flex", gap: 2 }}>
        {icons && (
          <Box>
            {icons === "hugging_face" ? (
              <img
                src={`/icons/datasets/${icons}.svg`}
                alt={icons}
                width={24}
                height={24}
              />
            ) : (
              <SvgColor
                src={`/icons/datasets/${icons}.svg`}
                sx={{ width: 24, height: 24, color: "text.primary" }}
              />
            )}
          </Box>
        )}
        <Box>
          <Box sx={{ display: "flex", alignItems: "center" }}>
            <Typography
              fontWeight={"fontWeightRegular"}
              color="text.primary"
              variant="s1"
            >
              {title}
            </Typography>
          </Box>
          <Typography
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
            variant="s2"
          >
            {subTitle}
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

DatasetOptions.propTypes = {
  title: PropTypes.string,
  subTitle: PropTypes.string,
  onClick: PropTypes.func,
  icons: PropTypes.string,
};

export default DatasetOptions;
