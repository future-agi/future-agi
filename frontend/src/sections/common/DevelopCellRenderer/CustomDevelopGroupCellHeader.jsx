import { alpha, Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const CustomDevelopGroupCellHeader = (props) => {
  const { displayName, col } = props;
  const theme = useTheme();

  const renderIcon = () => {
    if (col.originType === "run_prompt") {
      return <Iconify icon="token:rune" sx={{ color: "info.main" }} />;
    } else if (col.originType === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "#22B3B7" }}
        />
      );
    } else if (col.originType === "optimisation") {
      return (
        <Iconify
          icon="icon-park-outline:smart-optimization"
          sx={{ color: "primary.main" }}
        />
      );
    } else if (col.dataType === "text") {
      return <Iconify icon="material-symbols:notes" />;
    } else if (col.dataType === "array") {
      return <Iconify icon="material-symbols:data-array" />;
    } else if (col.dataType === "integer") {
      return <Iconify icon="material-symbols:tag" />;
    } else if (col.dataType === "float") {
      return <Iconify icon="tabler:decimal" />;
    } else if (col.dataType === "boolean") {
      return <Iconify icon="material-symbols:toggle-on-outline" />;
    } else if (col.dataType === "datetime") {
      return <Iconify icon="tabler:calendar" />;
    } else if (col.dataType === "json") {
      return <Iconify icon="material-symbols:data-object" />;
    } else if (col.dataType === "image") {
      return <Iconify icon="material-symbols:image-outline" />;
    } else if (col.dataType === "images") {
      return (
        <Iconify
          icon="material-symbols:art-track-outline"
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  const getBackgroundColor = () => {
    const isDark = theme.palette.mode === "dark";
    if (col.originType === "run_prompt") {
      return isDark ? alpha(theme.palette.info.main, 0.18) : "info.lighter";
    } else if (col.originType === "evaluation") {
      return "var(--surface-highlight)";
    } else if (col.originType === "optimisation") {
      return isDark
        ? alpha(theme.palette.primary.main, 0.16)
        : "primary.lighter";
    }

    return "background.default";
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        backgroundColor: getBackgroundColor(),
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {renderIcon()}
        <Typography fontWeight={700} fontSize="13px" color={"text.secondary"}>
          {displayName}
        </Typography>
      </Box>
    </Box>
  );
};

CustomDevelopGroupCellHeader.propTypes = {
  displayName: PropTypes.string,
  col: PropTypes.object,
};

export default CustomDevelopGroupCellHeader;
