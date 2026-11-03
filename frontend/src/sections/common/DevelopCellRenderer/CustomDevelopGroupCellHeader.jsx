import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const CustomDevelopGroupCellHeader = (props) => {
  const { displayName, col } = props;

  const renderIcon = () => {
    if (col.originType === "run_prompt") {
      return <Iconify icon="token:rune" sx={{ color: "info.main" }} />;
    } else if (col.originType === "evaluation") {
      return (
        <Iconify icon="solar:check-circle-linear" sx={{ color: "status.success.fg" }} />
      );
    } else if (col.originType === "optimisation") {
      return (
        <Iconify
          icon="solar:magic-stick-3-linear"
          sx={{ color: "primary.main" }}
        />
      );
    } else if (col.dataType === "text") {
      return <Iconify icon="solar:notes-linear" />;
    } else if (col.dataType === "array") {
      return <Iconify icon="solar:brackets-linear" />;
    } else if (col.dataType === "integer") {
      return <Iconify icon="solar:hashtag-linear" />;
    } else if (col.dataType === "float") {
      return <Iconify icon="tabler:decimal" />;
    } else if (col.dataType === "boolean") {
      return <Iconify icon="material-symbols:toggle-on-outline" />;
    } else if (col.dataType === "datetime") {
      return <Iconify icon="solar:calendar-linear" />;
    } else if (col.dataType === "json") {
      return <Iconify icon="solar:braces-linear" />;
    } else if (col.dataType === "image") {
      return <Iconify icon="solar:gallery-linear" />;
    } else if (col.dataType === "images") {
      return (
        <Iconify
          icon="solar:gallery-linear"
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  const getBackgroundColor = () => {
    if (col.originType === "run_prompt") {
      return "status.info.bg";
    } else if (col.originType === "evaluation") {
      return "var(--surface-highlight)";
    } else if (col.originType === "optimisation") {
      return "status.discovery.bg";
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
        <Typography fontWeight={600} fontSize="13px" color={"text.secondary"}>
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
