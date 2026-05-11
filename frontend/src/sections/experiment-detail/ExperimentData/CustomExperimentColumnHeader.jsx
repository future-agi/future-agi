import { Box, Button, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show/ShowComponent";
import SvgColor from "src/components/svg-color";
import { getUniqueColorPalette } from "src/utils/utils";
import { useRerunColumnInExperimentStoreShallow } from "./states";
import { EXPERIMENT_COLUMN_STATUS } from "./constants";

const iconStyle = {
  color: "text.secondary",
};
export const CustomExperimentColumnHeader = (props) => {
  const { displayName, showColumnMenu, col, hideMenu, head, index } = props;
  const refButton = useRef(null);

  const originType = col?.originType;
  const onMenuClicked = () => {
    showColumnMenu(refButton?.current);
  };

  const setSelectedSourceId = useRerunColumnInExperimentStoreShallow(
    (state) => state?.setSelectedSourceId,
  );
  const renderIcon = () => {
    if (props?.isGrouped) {
      const color = getUniqueColorPalette(index ? index - 1 : 0);
      return (
        <Box
          sx={{
            px: head ? "9px" : "0",
            py: head ? "3px" : "0",
            borderRadius: "4px",
            marginRight: head ? "9px" : "0",
            backgroundColor: color?.tagBackground,
          }}
        >
          <Typography
            variant="s2_1"
            color={color?.tagForeground}
            fontWeight={"fontWeightMedium"}
          >
            {head}
          </Typography>
        </Box>
      );
    } else if (col.originType === "run_prompt") {
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
    }
  };

  return (
    <Box
      id="bla"
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        "&:hover .rerun-button": {
          display: "flex",
          backgroundColor: "background.neutral !important",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
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
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="mdi:dots-vertical" />
        </IconButton>
      )}
      <ShowComponent condition={originType === "experiment"}>
        <Button
          className="rerun-button"
          startIcon={<SvgColor src="/assets/icons/navbar/ic_evaluate.svg" />}
          variant="outlined"
          size="small"
          color="primary"
          onClick={() => {
            setSelectedSourceId(col?.sourceId);
          }}
          disabled={[
            EXPERIMENT_COLUMN_STATUS.RUNNING,
            EXPERIMENT_COLUMN_STATUS.NOT_STARTED,
            EXPERIMENT_COLUMN_STATUS.QUEUED,
          ].includes(col?.status)}
          sx={{
            display: "none",
            ":hover ": {
              display: "flex",
            },
          }}
        >
          Re run
        </Button>
      </ShowComponent>
    </Box>
  );
};

CustomExperimentColumnHeader.propTypes = {
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
  isGrouped: PropTypes.bool,
  head: PropTypes.string,
  index: PropTypes.number,
};

export default CustomExperimentColumnHeader;
