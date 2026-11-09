import { Box, Button, Switch, Typography } from "@mui/material";
import React, { useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { useExperimentDetailContext } from "../experiment-context";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import { defaultRowHeightMapping } from "src/utils/constants";
import { shouldShowDiffModeButton } from "../ExperimentData/Common";
import DisplayMenu from "./DisplayMenu";
import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";
// J09 restores Add Evaluations (including experiment-scoped groups).
// L3: Edit stays unchanged. Download is hidden too: on dev its CSV
// (DownloadExperimentsView) reads only V1's experiments_datasets link, so a
// V2 experiment's file holds the dataset columns and none of the outputs or
// scores. Display stays. The Show Diff toggle
// is dev's, unchanged, and never shows on the V2 detail (shouldShowDiffModeButton
// reads camelCase fields the API sends in snake_case), as on dev: it is not a
// launch option.
const ExperimentBarDataRightSection = ({
  outputFormat,
  gridApiRef,
  experimentData,
  onAddEvaluations,
}) => {
  const {
    diffMode,
    handleToggleDiffMode,
    setExperimentDetailColumnSize,
    setShowAllColumns,
    showAllColumns,
    viewAllPrompts,
    setViewAllPrompts,
  } = useExperimentDetailContext();
  const displayMenuRef = useRef();
  const [openDisplayMenu, setOpenDisplayMenu] = useState(false);
  const [currentRowHeight, setCurrentRowHeight] = useState("Short");

  const hasBaseColumn = shouldShowDiffModeButton(experimentData);
  const handleHeightSelect = (mappingObject) => {
    const height = mappingObject.height;
    if (gridApiRef && gridApiRef.current) {
      gridApiRef?.current?.api.forEachNode((node) => {
        node.setRowHeight(height);
      });
    }

    gridApiRef?.current?.api.onRowHeightChanged();
  };

  const handleRowHeightChange = (heightKey) => {
    setCurrentRowHeight(heightKey);
    const mappingObject = defaultRowHeightMapping[heightKey];
    setExperimentDetailColumnSize(heightKey);
    handleHeightSelect(mappingObject);
  };
  const disabledDiffMode = outputFormat !== "string";
  return (
    <Box display="flex" alignItems="center" flexDirection={"row"} gap={1.5}>
      <ShowComponent condition={diffMode}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <SvgColor
            src="/icons/datasets/diff_pencil.svg"
            sx={{ height: "16px", width: "16px", color: "red.500" }}
          />
          <Typography
            variant="s3"
            color="text.primary"
            fontWeight={"fontWeightRegular"}
          >
            Missing Text
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <SvgColor
            src="/icons/datasets/diff_pencil.svg"
            sx={{ height: "16px", width: "16px", color: "green.500" }}
          />
          <Typography
            variant="s3"
            color="text.primary"
            fontWeight={"fontWeightRegular"}
          >
            New Text
          </Typography>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!disabledDiffMode && hasBaseColumn}>
        <Button
          size="medium"
          variant="outlined"
          onClick={handleToggleDiffMode}
          sx={{
            display: "flex",
            alignItems: "center",
            color: disabledDiffMode ? "black.400" : "black.1000",
            cursor: disabledDiffMode ? "not-allowed" : "pointer",
            padding: "8px 8px",
          }}
          disabled={disabledDiffMode}
        >
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            sx={{
              color: disabledDiffMode ? "text.disabled" : "text.primary",
            }}
          >
            Show Diff
          </Typography>
          <Switch
            size="small"
            sx={{
              "& .Mui-checked+.MuiSwitch-track": {
                backgroundColor: (theme) =>
                  `${theme.palette.primary.main} !important`,
              },
            }}
            checked={diffMode}
            disabled={disabledDiffMode}
          />
        </Button>
      </ShowComponent>

      <Button variant="outlined" onClick={onAddEvaluations}>
        {CLASSIC_EXPERIMENT_COPY.addEvaluations}
      </Button>

      <Button
        ref={displayMenuRef}
        onClick={() => setOpenDisplayMenu(true)}
        variant="outlined"
        size="medium"
        startIcon={
          <SvgColor
            src="/assets/icons/ic_display.svg"
            height={"16px"}
            width={"16px"}
          />
        }
        endIcon={
          <Iconify icon="mdi:chevron-down" height={"16px"} width={"16px"} />
        }
        sx={{ padding: "8px 12px" }}
      >
        <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
          Display
        </Typography>
      </Button>

      <DisplayMenu
        anchorEl={displayMenuRef.current}
        open={openDisplayMenu}
        onClose={() => setOpenDisplayMenu(false)}
        viewAllPrompts={viewAllPrompts}
        onViewAllPromptsChange={setViewAllPrompts}
        showAllColumns={showAllColumns}
        onShowAllColumnsChange={setShowAllColumns}
        rowHeightMapping={defaultRowHeightMapping}
        currentRowHeight={currentRowHeight}
        onRowHeightChange={handleRowHeightChange}
        hasAgentConfigs={!!experimentData?.agent_configs?.length}
      />
    </Box>
  );
};

export default ExperimentBarDataRightSection;

ExperimentBarDataRightSection.propTypes = {
  outputFormat: PropTypes.string,
  gridApiRef: PropTypes.object,
  experimentData: PropTypes.object,
  onAddEvaluations: PropTypes.func,
};
