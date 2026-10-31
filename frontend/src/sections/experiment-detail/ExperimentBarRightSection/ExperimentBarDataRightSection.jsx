import { Box, Button, IconButton, Switch, Typography } from "@mui/material";
import React, { useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { useExperimentDetailContext } from "../experiment-context";
import { useParams } from "react-router";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import PropTypes from "prop-types";
import { defaultRowHeightMapping } from "src/utils/constants";
import RunExperimentWithProvider from "src/sections/develop-detail/Experiment/RunExperiment";
import { useRunExperimentStoreShallow } from "src/sections/develop-detail/states";
import { shouldShowDiffModeButton } from "../ExperimentData/Common";
import DisplayMenu from "./DisplayMenu";
const ExperimentBarDataRightSection = ({
  outputFormat,
  gridApiRef,
  columns,
  experimentData,
}) => {
  const { experimentId } = useParams();
  const queryClient = useQueryClient();
  const allowedColumns = columns.filter((col) =>
    ["OTHERS", "run_prompt"].includes(col?.originType),
  );
  const {
    setEvaluateOpen,
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
  const { mutate: downloadExperiment } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.develop.experiment.downloadExperiment(experimentId), {
        responseType: "blob",
      }),
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `experiment-${experimentId}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Experiment downloaded successfully", {
        variant: "success",
      });
    },
  });
  const disabledDiffMode = outputFormat !== "string";
  const { initiateEditMode } = useRunExperimentStoreShallow((state) => ({
    initiateEditMode: state.initiateEditMode,
  }));
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

      <Button
        variant="outlined"
        size="medium"
        sx={{ padding: "8px 12px" }}
        startIcon={
          <SvgColor
            src="/assets/icons/ic_edit_pencil.svg"
            sx={{ height: "16px", width: "16px" }}
          />
        }
        onClick={() => {
          initiateEditMode(experimentId);
        }}
      >
        <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
          Edit
        </Typography>
      </Button>

      <IconButton
        size="small"
        sx={{
          color: "text.primary",
          borderRadius: 0.75,
          border: "1px solid",
          height: "38px",
          width: "38px",
          borderColor: "divider",
        }}
        onClick={() => downloadExperiment()}
      >
        <Iconify icon="material-symbols:download" color="text.primary" />
      </IconButton>
      <Button
        variant="contained"
        color="primary"
        sx={{ padding: "8px 12px" }}
        startIcon={<Iconify icon="mdi:plus" />}
        onClick={() => setEvaluateOpen(true)}
      >
        <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
          Add Evaluations
        </Typography>
      </Button>
      <RunExperimentWithProvider
        onSuccessfulSubmit={() => {
          gridApiRef?.current?.api?.refreshServerSide({ force: true });
          queryClient.invalidateQueries({
            queryKey: ["experiment-column-config", experimentId],
            type: "all",
          });
        }}
        experimentColumns={allowedColumns}
        tabRef={gridApiRef}
      />
    </Box>
  );
};

export default ExperimentBarDataRightSection;

ExperimentBarDataRightSection.propTypes = {
  outputFormat: PropTypes.string,
  gridApiRef: PropTypes.object,
  columns: PropTypes.array,
  experimentData: PropTypes.object,
};
