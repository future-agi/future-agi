import { alpha, Box, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useEffect } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

export const CustomDevelopDetailColumn = (props) => {
  const { displayName, showColumnMenu, col, hideMenu, eGridHeader, api } =
    props;
  const refButton = useRef(null);
  const theme = useTheme();

  useEffect(() => {
    eGridHeader.style.padding = "0px";
  }, [eGridHeader.style]);

  useEffect(() => {
    if (api && col) {
      const minWidth = Math.max(displayName.length * 8 + 50, 250);
      props.api.setColumnWidths([{ key: col.id, newWidth: minWidth }]);
    }
  }, [api, col, displayName, props.api]);

  const onMenuClicked = () => {
    showColumnMenu(refButton?.current);
  };

  const iconStyle = {
    color: "text.secondary",
  };

  const renderIcon = () => {
    if (col.originType === "run_prompt") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{ width: 20, height: 20, color: "info.main" }}
        />
      );
    } else if (col.originType === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "info.success" }}
        />
      );
    } else if (
      col.originType === "optimisation" ||
      col.originType === "optimisation_evaluation"
    ) {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_optimize.svg`}
          sx={{ width: 20, height: 20, color: "primary.main" }}
        />
      );
    } else if (col.originType === "annotation_label") {
      return <Iconify icon="jam:write" sx={iconStyle} />;
    } else if (col.originType === "vector_db") {
      return <Iconify icon="solar:widget-broken" sx={iconStyle} />;
    } else if (col.originType === "extracted_entities") {
      return <Iconify icon="material-symbols:chip-extraction" sx={iconStyle} />;
    } else if (col.originType === "extracted_json") {
      return <Iconify icon="mdi:code-json" sx={iconStyle} />;
    } else if (col.originType === "python_code") {
      return <Iconify icon="mdi:code" sx={iconStyle} />;
    } else if (col.originType === "classification") {
      return <Iconify icon="mingcute:classify-2-line" sx={iconStyle} />;
    } else if (col.originType === "api_call") {
      return <Iconify icon="hugeicons:api" sx={iconStyle} />;
    } else if (col.originType === "conditional") {
      return <Iconify icon="hugeicons:node-add" sx={iconStyle} />;
    } else if (col.dataType === "text") {
      return <Iconify icon="material-symbols:notes" sx={iconStyle} />;
    } else if (col.dataType === "array") {
      return <Iconify icon="material-symbols:data-array" sx={iconStyle} />;
    } else if (col.dataType === "integer") {
      return <Iconify icon="material-symbols:tag" sx={iconStyle} />;
    } else if (col.dataType === "float") {
      return <Iconify icon="tabler:decimal" sx={iconStyle} />;
    } else if (col.dataType === "boolean") {
      return (
        <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
      );
    } else if (col.dataType === "datetime") {
      return <Iconify icon="tabler:calendar" sx={iconStyle} />;
    } else if (col.dataType === "json") {
      return <Iconify icon="material-symbols:data-object" sx={iconStyle} />;
    } else if (col.dataType === "image") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_image.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (col.dataType === "audio") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_audio.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (col.dataType === "document") {
      return (
        <SvgColor
          src={`/assets/icons/files/ic_file_head.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (col.dataType === "images") {
      return (
        <Iconify
          icon="material-symbols:art-track-outline"
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  const getBackgroundColor = (originType) => {
    const isDark = theme.palette.mode === "dark";
    if (
      originType === "evaluation" ||
      originType === "optimisation_evaluation"
    ) {
      return "var(--surface-highlight)";
    } else if (originType === "run_prompt") {
      return isDark ? alpha(theme.palette.info.main, 0.18) : "info.lighter";
    } else if (originType === "optimisation") {
      return isDark
        ? alpha(theme.palette.primary.main, 0.16)
        : "primary.lighter";
    } else if (originType === "annotation_label") {
      return isDark
        ? alpha(theme.palette.primary.main, 0.16)
        : "primary.lighter";
    }
    return isDark ? "background.neutral" : "whiteScale.100";
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        backgroundColor: getBackgroundColor(),
        paddingX: 1,
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {renderIcon()}
        <Typography fontWeight={500} fontSize="14px" color={"text.secondary"}>
          {displayName}
        </Typography>
      </Box>
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="mdi:dots-vertical" />
        </IconButton>
      )}
    </Box>
  );
};

CustomDevelopDetailColumn.propTypes = {
  displayName: PropTypes.string.isRequired,
  eSort: PropTypes.object,
  eMenu: PropTypes.object,
  eFilterButton: PropTypes.object,
  eFilter: PropTypes.object,
  eSortOrder: PropTypes.object,
  eSortAsc: PropTypes.object,
  eSortDesc: PropTypes.object,
  eSortNone: PropTypes.object,
  eText: PropTypes.object,
  menuButtonRef: PropTypes.object,
  filterButtonRef: PropTypes.object,
  sortOrderRef: PropTypes.object,
  sortAscRef: PropTypes.object,
  sortDescRef: PropTypes.object,
  sortNoneRef: PropTypes.object,
  filterRef: PropTypes.object,
  showColumnMenu: PropTypes.func,
  col: PropTypes.object,
  hideMenu: PropTypes.bool,
  eGridHeader: PropTypes.any,
  api: PropTypes.any,
};

export default CustomDevelopDetailColumn;
