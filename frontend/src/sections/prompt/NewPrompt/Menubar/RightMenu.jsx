import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Divider,
  Drawer,
  FormControl,
  IconButton,
  MenuItem,
  Modal,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";
import React, { useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import TemplateFormatSelector from "src/sections/workbench/createPrompt/Playground/TemplateFormatSelector";
import RunEvaluation from "src/sections/develop-detail/Evaluation/RunEvaluation";
import PropTypes from "prop-types";
import { action } from "src/theme/palette";
import { trackEvent, Events } from "src/utils/Mixpanel";

import Versions from "../TopMenuOptions/Versions";
import Variables from "../TopMenuOptions/Variables";
import ModelSettings from "../TopMenuOptions/ModelSettings";

const RightMenu = ({
  handleLabelsAdd,
  handleModelSettingData,
  variables,
  resultState,
  versionList,
  evalsConfigs,
  setEvalsConfigs,
  setExtractedVars,
  initialConfig,
  appliedVariableData,
  setAppliedVariableData,
  versionIndex,
  setVersionIndex,
  setVersionList,
  total,
  currentTitle,
  templateFormat,
  setTemplateFormat,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const gridApiRef = useRef(null);
  const [selectedLabel, setSelectedLabel] = useState(null);
  const [open, setOpen] = useState(false);
  const [drawerWidth, setDrawerWidth] = useState("550px");
  const [configModal, setConfigureModal] = useState(false);
  const [openEvals, setOpenEvals] = useState(false);

  const refreshGrid = (options) => {
    gridApiRef.current?.api?.refreshServerSide(options);
  };

  const handleClose = () => {
    setConfigureModal(false);
  };

  const handleModelSettingDataWithState = (data) => {
    handleModelSettingData(data);
  };

  const generateAllColumns = (variables) => {
    const fixedColumns = [
      {
        headerName: "model_input",
        field: "input_prompt",
      },
      {
        headerName: "model_output",
        field: "output_prompt",
      },
    ];

    const variableColumns = variables.map((variable) => ({
      headerName: variable,
      field: variable,
    }));

    return [...fixedColumns, ...variableColumns];
  };

  const allColumns = generateAllColumns(variables);

  const ICON_BTNS = [
    {
      label: "Model Settings",
      color: "info.main",
      icon: "codicon:settings",
    },
    {
      label: "Variables",
      color: "info.primary",
      icon: "tdesign:brackets",
    },
    {
      label: "Evaluate",
      color: "info.success",
      icon: "material-symbols:check-circle-outline",
    },
  ];

  const handleButtonClick = (label) => {
    if (label !== "Evaluate") {
      setOpen(true);
      setSelectedLabel(label);
      setDrawerWidth(
        label === "Variables" && variables.length ? "700px" : "550px",
      );
      trackEvent(
        label === "Model Settings"
          ? Events.modelSettingsClicked
          : Events.variableDrawerClicked,
      );
    } else {
      if (!initialConfig?.model) {
        enqueueSnackbar(
          "Please select a model from model settings before proceeding.",
          {
            variant: "warning",
          },
        );
        return;
      }
      if (versionList.length === 1) {
        if (versionList[0].isDraft === true) {
          enqueueSnackbar(
            "Run atleast one version of prompt to evaluate your responses",
            {
              variant: "warning",
            },
          );
          return;
        } else {
          setOpenEvals(true);
        }
      } else {
        // **Ensure the evaluation modal opens**
        setOpenEvals(true);
      }
    }
  };

  const memoisedDrawerComponent = useMemo(() => {
    switch (selectedLabel) {
      case "Variables":
        return (
          <Variables
            variables={variables}
            onClose={() => {
              setOpen(false);
            }}
            setExtractedVars={setExtractedVars}
            handleLabelsAdd={handleLabelsAdd}
            appliedVariableData={appliedVariableData}
            setAppliedVariableData={setAppliedVariableData}
            currentTitle={currentTitle}
          />
        );
      case "Model Settings":
        return (
          <ModelSettings
            onClose={() => setOpen(false)}
            handleModelSettingData={handleModelSettingDataWithState}
            initialConfig={initialConfig}
            handleLabelsAdd={handleLabelsAdd}
          />
        );
      case "Versions":
        return (
          <Versions
            versionList={versionList}
            onClose={() => setOpen(false)}
            versionIndex={versionIndex}
            setVersionIndex={setVersionIndex}
            setVersionList={setVersionList}
            total={total}
          />
        );
      default:
        return <></>;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    selectedLabel,
    handleLabelsAdd,
    handleModelSettingData,
    variables,
    resultState,
    versionList,
    evalsConfigs,
    setEvalsConfigs,
    setExtractedVars,
    initialConfig,
    appliedVariableData,
    setAppliedVariableData,
    versionIndex,
    setVersionIndex,
    open,
    currentTitle,
  ]);

  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <Tooltip title={"Versions"} placement="bottom" arrow>
          <IconButton
            onClick={() => {
              setOpen(true);
              setDrawerWidth("550px");
              setSelectedLabel("Versions");
            }}
          >
            <Iconify
              sx={{ cursor: "pointer" }}
              icon="stash:version"
              color="text.disabled"
            />
          </IconButton>
        </Tooltip>
        {/* <Tooltip title={'Configure'} placement="bottom" arrow>
          <IconButton
            onClick={() => {
              setConfigureModal(true);
            }}
        >
          <Iconify
            sx={{ cursor: "pointer" }}
            icon="mdi:code"
              color="text.disabled"
            />
          </IconButton>
        </Tooltip> */}
      </Box>

      <Divider
        sx={{ borderWidth: "1px", height: "21px", margin: "auto 12px" }}
        orientation="vertical"
        flexItem
      />

      <TemplateFormatSelector
        value={templateFormat}
        onChange={setTemplateFormat}
      />

      <Box sx={{ display: "flex", alignItems: "center", gap: "4px" }}>
        {ICON_BTNS.map(({ label, icon, color }) => (
          <Tooltip key={label} title={label} placement="bottom" arrow>
            <Button
              variant="text"
              size="small"
              startIcon={<Iconify icon={icon} />}
              onClick={() => handleButtonClick(label)}
              sx={{ color, padding: "5px 8px" }}
            >
              {label}
            </Button>
          </Tooltip>
        ))}
      </Box>

      <Box sx={{ display: "flex", alignItems: "center", margin: "0 0 0 10px" }}>
        <LoadingButton
          fullWidth
          size="medium"
          variant="contained"
          sx={{ padding: "6px 24px", fontSize: "14px" }}
          color="primary"
          onClick={() => {
            handleLabelsAdd("prompt");
          }}
          // disabled
          loading={resultState === "Running"}
        >
          Save and Run
        </LoadingButton>
      </Box>

      <Drawer
        anchor="right"
        open={open}
        onClose={() => setOpen(false)}
        PaperProps={{
          sx: {
            height: "100vh",
            width: drawerWidth,
            position: "fixed",
            zIndex: 9999,
            borderRadius: "10px",
            backgroundColor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        {memoisedDrawerComponent}
        {/* {selectedLabel} */}
      </Drawer>

      <RunEvaluation
        open={openEvals}
        module="prompt"
        hideSaveAndRun={true}
        handleLabelsAdd={handleLabelsAdd}
        onClose={() => setOpenEvals(false)}
        allColumns={allColumns}
        refreshGrid={refreshGrid}
        datasetId={
          versionList[0]?.originalTemplate ??
          "657d8f7f-0b21-478d-967c-36e3b7affa9f"
        }
        evalsConfigs={evalsConfigs}
        setEvalsConfigs={setEvalsConfigs}
      />
      <Modal
        open={configModal}
        onClose={handleClose}
        sx={{ display: "flex", alignItems: "center" }}
      >
        <Box
          sx={{
            width: "100%",
            maxWidth: "926px",
            bgcolor: "background.paper",
            boxShadow: 24,
            borderRadius: "16px",
            mx: "auto",
          }}
        >
          <Stack
            flexDirection="row"
            alignItems="center"
            justifyContent="space-between"
            p="13px 24px"
          >
            <Typography>Configure a tool</Typography>
            <IconButton onClick={handleClose}>
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Stack>
          <Box sx={{ px: "24px", pb: "30px" }}>
            <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
              <Iconify icon="solar:info-circle-bold" color="text.disabled" />
              <Typography variant="caption" color="text.secondary">
                You can generate a structured prompt by sharing basic details
                about your task
              </Typography>
            </Box>

            <FormControl sx={{ mt: "25px" }}>
              <Select
                defaultValue="Python"
                size="small"
                displayEmpty
                sx={{ minWidth: 200 }}
              >
                <MenuItem value="Python">Python</MenuItem>
                <MenuItem value="C++">C++</MenuItem>
                <MenuItem value="React">React</MenuItem>
              </Select>
            </FormControl>

            <Stack flexDirection="row" justifyContent="flex-end" mb={"10px"}>
              <Button
                onClick={handleClose}
                variant="outline"
                startIcon={<Iconify icon="basil:copy-outline" />}
                color="primary"
              >
                Copy
              </Button>
            </Stack>

            <TextField
              sx={{ background: action.hover }}
              multiline
              rows={10}
              placeholder="#Add variable to Data , if any"
              //  value={inputs.prompt}
              //  onChange={handleInputChange("prompt")}
              fullWidth
            />

            <Stack flexDirection="row" justifyContent="flex-end" pt="30px">
              <Button
                onClick={handleClose}
                variant="contained"
                sx={{ mt: 2 }}
                color="primary"
              >
                Close
              </Button>
            </Stack>
          </Box>
        </Box>
      </Modal>
    </Box>
  );
};

export default RightMenu;

RightMenu.propTypes = {
  handleLabelsAdd: PropTypes.func,
  versionList: PropTypes.arrayOf(Object),
  resultState: PropTypes.string,
  handleModelSettingData: PropTypes.any,
  variables: PropTypes.any,
  titles: PropTypes.any,
  setTitles: PropTypes.any,
  currentIndex: PropTypes.any,
  setCurrentIndex: PropTypes.any,
  setExtractedVars: PropTypes.any,
  initialConfig: PropTypes.object,
  evalsConfigs: PropTypes.array,
  setEvalsConfigs: PropTypes.func,
  appliedVariableData: PropTypes.any,
  setAppliedVariableData: PropTypes.any,
  versionIndex: PropTypes.number,
  setVersionList: PropTypes.func,
  setVersionIndex: PropTypes.func,
  total: PropTypes.number,
  currentTitle: PropTypes.string,
  templateFormat: PropTypes.string,
  setTemplateFormat: PropTypes.func,
};
