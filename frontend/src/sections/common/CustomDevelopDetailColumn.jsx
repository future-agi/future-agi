import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useEffect } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

export const CustomDevelopDetailColumn = (props) => {
  const { displayName, showColumnMenu, col, hideMenu, eGridHeader, api } =
    props;
  const refButton = useRef(null);

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
          icon="solar:check-circle-linear"
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
      return <Iconify icon="solar:pen-linear" sx={iconStyle} />;
    } else if (col.originType === "vector_db") {
      return <Iconify icon="solar:widget-broken" sx={iconStyle} />;
    } else if (col.originType === "extracted_entities") {
      return <Iconify icon="solar:cpu-linear" sx={iconStyle} />;
    } else if (col.originType === "extracted_json") {
      return <Iconify icon="solar:code-linear" sx={iconStyle} />;
    } else if (col.originType === "python_code") {
      return <Iconify icon="solar:code-linear" sx={iconStyle} />;
    } else if (col.originType === "classification") {
      return <Iconify icon="solar:tag-horizontal-linear" sx={iconStyle} />;
    } else if (col.originType === "api_call") {
      return <Iconify icon="solar:plug-circle-linear" sx={iconStyle} />;
    } else if (col.originType === "conditional") {
      return <Iconify icon="solar:widget-add-linear" sx={iconStyle} />;
    } else if (col.dataType === "text") {
      return <Iconify icon="solar:notes-linear" sx={iconStyle} />;
    } else if (col.dataType === "array") {
      return <Iconify icon="solar:brackets-linear" sx={iconStyle} />;
    } else if (col.dataType === "integer") {
      return <Iconify icon="solar:hashtag-linear" sx={iconStyle} />;
    } else if (col.dataType === "float") {
      return <Iconify icon="tabler:decimal" sx={iconStyle} />;
    } else if (col.dataType === "boolean") {
      return (
        <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
      );
    } else if (col.dataType === "datetime") {
      return <Iconify icon="solar:calendar-linear" sx={iconStyle} />;
    } else if (col.dataType === "json") {
      return <Iconify icon="solar:braces-linear" sx={iconStyle} />;
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
          icon="solar:gallery-linear"
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  const getBackgroundColor = (originType) => {
    if (
      originType === "evaluation" ||
      originType === "optimisation_evaluation"
    ) {
      return "var(--surface-highlight)";
    } else if (originType === "run_prompt") {
      return "status.info.bg";
    } else if (originType === "optimisation") {
      return "status.discovery.bg";
    } else if (originType === "annotation_label") {
      return "status.discovery.bg";
    }
    return "background.neutral";
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
        <Typography fontWeight={500} fontSize="13px" color={"text.secondary"}>
          {displayName}
        </Typography>
      </Box>
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="solar:menu-dots-vertical-linear" />
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
