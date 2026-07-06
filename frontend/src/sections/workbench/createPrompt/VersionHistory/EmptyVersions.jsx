import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SVGColor from "src/components/svg-color";

const EmptyVersions = ({ activeTab }) => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        flex: 1,
        gap: "14px",
      }}
    >
      <Box
        sx={{
          padding: 2,
          border: "2.3px solid",
          borderColor: "divider",
          borderRadius: "9.2px",
          lineHeight: 0,
        }}
      >
        <SVGColor
          src="/assets/icons/components/ic_version_history.svg"
          sx={{
            width: 36,
            height: 36,
            background: `linear-gradient(0deg, ${theme.palette.primary.main} 0%, ${theme.palette.pink[500]} 100%)`,
          }}
        />
      </Box>
      <Typography
        variant="m3"
        fontWeight="fontWeightMedium"
        color="text.secondary"
        sx={{ width: "300px", textAlign: "center" }}
      >
        {activeTab === "commit_history"
          ? "Commit at least one prompt to view it in the version history."
          : "Run at least one prompt to view it in the version history."}
      </Typography>
    </Box>
  );
};

EmptyVersions.propTypes = {
  activeTab: PropTypes.string,
};

export default EmptyVersions;
