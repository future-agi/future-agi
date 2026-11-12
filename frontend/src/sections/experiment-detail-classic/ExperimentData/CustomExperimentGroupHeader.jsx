import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

const CustomExperimentGroupHeader = ({
  group,
  displayName,
  api: _api,
  columnGroup: _columnGroup,
  showColumnMenu, // <-- This is provided by AG Grid for group headers
}) => {
  const refButton = useRef(null);

  const onMenuClicked = (event) => {
    event.stopPropagation();

    // Use AG Grid's built-in showColumnMenu for column groups
    // This is passed as a prop to headerGroupComponent
    if (showColumnMenu && refButton.current) {
      showColumnMenu(refButton.current);
    }
  };

  const iconStyle = {
    color: "text.secondary",
  };

  const renderIcon = () => {
    if (group.origin === "Evaluation" || group.origin === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "info.success" }}
        />
      );
    } else if (group.origin === "run_prompt") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{ width: 20, height: 20, color: "info.main" }}
        />
      );
    } else if (
      group.origin === "optimisation" ||
      group.origin === "optimisation_evaluation" ||
      group.origin === "Optimisation"
    ) {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_optimize.svg`}
          sx={{ width: 20, height: 20, color: "primary.main" }}
        />
      );
    } else if (group.origin === "annotation_label") {
      return <Iconify icon="jam:write" sx={iconStyle} />;
    } else if (group.dataType === "text") {
      return <Iconify icon="material-symbols:notes" sx={iconStyle} />;
    } else if (group.dataType === "array") {
      return <Iconify icon="material-symbols:data-array" sx={iconStyle} />;
    } else if (group.dataType === "integer") {
      return <Iconify icon="material-symbols:tag" sx={iconStyle} />;
    } else if (group.dataType === "float") {
      return <Iconify icon="tabler:decimal" sx={iconStyle} />;
    } else if (group.dataType === "boolean") {
      return (
        <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
      );
    } else if (group.dataType === "datetime") {
      return <Iconify icon="tabler:calendar" sx={iconStyle} />;
    } else if (group.dataType === "json") {
      return <Iconify icon="material-symbols:data-object" sx={iconStyle} />;
    } else if (group.dataType === "image") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_image.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (group.dataType === "audio") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_audio.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 0.5, width: "100%" }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          maxWidth: "80%",
          overflow: "hidden",
        }}
      >
        {renderIcon()}
        <Typography
          fontWeight={700}
          fontSize="13px"
          color={"text.secondary"}
          sx={{
            textOverflow: "ellipsis",
            overflow: "hidden",
          }}
        >
          {displayName}
        </Typography>
      </Box>
      {group?.origin === "Evaluation" && (
        <IconButton
          sx={{
            ml: "auto",
          }}
          size="small"
          ref={refButton}
          onClick={onMenuClicked}
        >
          <Iconify icon="mdi:dots-vertical" />
        </IconButton>
      )}
    </Box>
  );
};

CustomExperimentGroupHeader.propTypes = {
  displayName: PropTypes.string,
  group: PropTypes.object,
  api: PropTypes.object,
  columnGroup: PropTypes.object,
  showColumnMenu: PropTypes.func,
};

export default CustomExperimentGroupHeader;
