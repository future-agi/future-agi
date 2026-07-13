import {
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  useTheme,
  Link,
} from "@mui/material";
import { useMutation, useQuery } from "@tanstack/react-query";
import React, { useState } from "react";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import AddRowDrawer from "src/sections/develop/AddRowDrawer/AddRowDrawer";
import AddColumnDrawer from "../AddColumn/AddColumnDrawer";
import DevelopDataSelectionActive from "./DevelopDataSelectionActive";
import SvgColor from "src/components/svg-color";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import {
  useRunPromptStoreShallow,
  useAddColumnDrawerStoreShallow,
  useDevelopSelectedRowsStoreShallow,
  useRunEvaluationStoreShallow,
  useDatasetOriginStore,
} from "../states";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import PropTypes from "prop-types";
import SyntheticSummaryDrawer from "../../develop/AddRowDrawer/EditSyntheticData/SyntheticSummaryDrawer";
import { useEditSyntheticDataStore } from "../../develop/AddRowDrawer/EditSyntheticData/state";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import CustomTooltip from "src/components/tooltip";

const DevelopDataRightSection = ({ hideScenarioFeatures = false }) => {
  const [addRowDrawerOpen, setAddRowDrawerOpen] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const setOpenAddColumnDrawer = useAddColumnDrawerStoreShallow(
    (s) => s.setOpenAddColumnDrawer,
  );
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  const { role } = useAuthContext();

  const setOpenRunEvaluation = useRunEvaluationStoreShallow(
    (s) => s.setOpenRunEvaluation,
  );
  const { setOpenSummaryDrawer } = useEditSyntheticDataStore();

  const theme = useTheme();
  const { dataset } = useParams();

  const { isSelected } = useDevelopSelectedRowsStoreShallow((s) => ({
    isSelected: s.selectAll || s.toggledNodes.length > 0,
  }));

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", {
      enabled: false,
      refetchInterval: (data) => {
        const isProcessing = data?.data?.result?.isProcessingData;

        if (isProcessing) return 2000;
        return false;
      },
    }),
  );

  const { processingComplete } = useDatasetOriginStore();

  const isData = Boolean(tableData?.data?.result?.table?.length);
  const isSyntheticDataset = Boolean(tableData?.data?.result?.synthetic_dataset);
  const processingData = Boolean(tableData?.data?.result?.isProcessingData);
  const buttonStyles = {
    color: "text.primary",
    border: "1px solid",
    fontSize: "12px",
    fontWeight: 400,
    lineHeight: "18px",
    borderColor: "divider",
    paddingY: theme.spacing(0.5),
    paddingX: theme.spacing(1.5),
  };

  const iconStyles = {
    width: 16,
    height: 16,
    color: "text.primary",
  };

  const datasetButtons = [
    {
      icon: "ic_configure",
      title: "Configure Synthetic Data",
      action: () => setOpenSummaryDrawer(true),
    },
    {
      icon: "ic_add_row",
      title: "Add Row",
      action: () => setAddRowDrawerOpen(true),
      event: Events.addRowsClicked,
    },
    {
      icon: "ic_add_column",
      title: "Add Column",
      action: () => setOpenAddColumnDrawer(true),
      event: Events.addColumnsClicked,
    },
  ];

  const promptButtons = [
    {
      icon: "ic_run_prompt",
      title: "Run Prompt",
      action: () => setOpenRunPrompt(true),
      event: Events.datasetRunPromptClicked,
      hoverText:
        "Execute your prompt using this dataset to see how it performs",
      link: "https://docs.futureagi.com/docs/dataset/features/run-prompt",
    },
    {
      icon: "ic_evaluate",
      title: "Evaluate",
      action: () => setOpenRunEvaluation(true),
      event: Events.datasetRunEvaluationClicked,
      hoverText:
        "Test your accuracy, reliability, and edge-case handling with structured checks.",
      link: "https://docs.futureagi.com/docs/evaluation/features/evaluate",
    },
  ];

  const { mutate: downloadDataset } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.develop.downloadDataset(dataset), {
        responseType: "blob",
      }),
    onMutate: () => {
      enqueueSnackbar("Download has been started...", { variant: "info" });
      setIsDownloading(true);
    },
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `dataset-${dataset}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Dataset downloaded successfully", {
        variant: "success",
      });
    },
    onSettled: () => {
      setIsDownloading(false);
    },
  });

  return (
    <React.Fragment>
      {isSelected ? (
        <DevelopDataSelectionActive />
      ) : (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: theme.spacing(1.5),
            ml: "auto",
          }}
        >
          <Divider
            orientation="vertical"
            flexItem
            sx={{ my: theme.spacing(0.5) }}
          />
          <Box sx={{ display: "flex", gap: theme.spacing(1.5) }}>
            {datasetButtons.map((button, index) => {
              if (
                button.title === "Configure Synthetic Data" &&
                !isSyntheticDataset
              )
                return;
              return (
                <Button
                  key={index}
                  size="small"
                  startIcon={
                    <SvgColor
                      src={`/assets/icons/action_buttons/${button.icon}.svg`}
                      sx={iconStyles}
                    />
                  }
                  onClick={() => {
                    button.action?.();
                    if (button?.event)
                      trackEvent(button.event, {
                        [PropertyName.id]: dataset,
                      });
                  }}
                  disabled={
                    !tableData ||
                    processingData ||
                    (!isData && button?.title !== "Add Row") ||
                    (isSyntheticDataset && !processingComplete) ||
                    !RolePermission.DATASETS[PERMISSIONS.UPDATE][role]
                  }
                  sx={buttonStyles}
                >
                  {button.title}
                </Button>
              );
            })}
          </Box>

          <Divider
            orientation="vertical"
            flexItem
            sx={{ my: theme.spacing(0.5) }}
          />

          <Box sx={{ display: "flex", gap: theme.spacing(1.5) }}>
            {!hideScenarioFeatures &&
              promptButtons.map((button, index) => (
                <CustomTooltip
                  key={index}
                  show={button.hoverText ? true : false}
                  title={
                    <Box>
                      {button.hoverText}{" "}
                      {button.link && (
                        <Link
                          href={button.link}
                          underline="always"
                          color="blue.500"
                          target="_blank"
                          rel="noopener noreferrer"
                          fontWeight="fontWeightMedium"
                          onClick={(e) => e.stopPropagation()}
                          sx={{ whiteSpace: "nowrap" }}
                        >
                          Read more
                        </Link>
                      )}
                    </Box>
                  }
                  placement="bottom"
                  arrow
                  size="small"
                  type="black"
                  slotProps={{
                    tooltip: {
                      sx: {
                        maxWidth: "200px !important",
                      },
                    },
                  }}
                >
                  <Button
                    key={index}
                    size="small"
                    startIcon={
                      <SvgColor
                        src={`/assets/icons/action_buttons/${button.icon}.svg`}
                        sx={iconStyles}
                      />
                    }
                    onClick={() => {
                      button.action?.();
                      if (button.event)
                        trackEvent(button.event, {
                          [PropertyName.id]: dataset,
                        });
                    }}
                    disabled={
                      !isData ||
                      (isSyntheticDataset && !processingComplete) ||
                      !RolePermission.DATASETS[PERMISSIONS.UPDATE][role]
                    }
                    sx={buttonStyles}
                  >
                    {button.title}
                  </Button>
                </CustomTooltip>
              ))}
          </Box>

          <Divider
            orientation="vertical"
            flexItem
            sx={{ my: theme.spacing(0.5) }}
          />
          {isDownloading ? (
            <CircularProgress size={20} />
          ) : (
            <IconButton
              size="small"
              sx={{ p: theme.spacing(0) }}
              disabled={!isData || (isSyntheticDataset && !processingComplete)}
              onClick={() => {
                trackEvent(Events.datasetDownloadClicked);
                downloadDataset();
              }}
            >
              <SvgColor
                src="/assets/icons/action_buttons/ic_download.svg"
                sx={{
                  width: 20,
                  height: 20,
                  color: !isData ? "text.disabled" : "text.primary",
                }}
              />
            </IconButton>
          )}
        </Box>
      )}
      <AddRowDrawer
        open={addRowDrawerOpen}
        onClose={() => setAddRowDrawerOpen(false)}
      />
      <AddColumnDrawer hideScenarioFeatures={hideScenarioFeatures} />
      <SyntheticSummaryDrawer />
    </React.Fragment>
  );
};

DevelopDataRightSection.displayName = "DevelopDataRightSection";

DevelopDataRightSection.propTypes = {
  hideScenarioFeatures: PropTypes.bool,
};

export default DevelopDataRightSection;
