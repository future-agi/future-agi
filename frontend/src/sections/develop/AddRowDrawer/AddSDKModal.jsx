import {
  Box,
  Button,
  IconButton,
  Modal,
  Typography,
  useTheme,
  CircularProgress,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { LoadingButton } from "@mui/lab";
import { CustomTab, CustomTabs, TabWrapper } from "./AddDatasetStyle";
import { ShowComponent } from "src/components/show";
import { FormCodeEditor } from "src/components/form-code-editor";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { enqueueSnackbar } from "notistack";

const defaultValues = {
  name: "",
  Pcode: "",
  Tcode: "",
  CUcode: "",
};

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  minimap: {
    enabled: false,
  },
  wordWrap: "on",
  theme: "light",
};

const tabOptions = [
  { label: "Python", value: "python", disabled: false },
  { label: "Typescript", value: "typescript", disabled: false },
  { label: "Curl", value: "curl", disabled: false },
];

const AddSDKModal = ({ open, onClose, datasetId, closeDrawer }) => {
  const theme = useTheme();
  const { control, reset, setValue } = useForm({
    defaultValues,
  });

  const [, setIsNext] = useState(false);
  const [currentTab, setCurrentTab] = useState("python");

  const copyToClipboard = (text, type) => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        enqueueSnackbar(`${type} copied to clipboard!`, { variant: "success" });
      })
      .catch(() => {
        enqueueSnackbar(`Failed to copy ${type}`, { variant: "error" });
      });
  };

  const { data, isFetching } = useQuery({
    queryKey: ["addRowSDK", datasetId],
    queryFn: async () => {
      const response = await axios.post(endpoints.row.addRowSdk, {
        dataset_id: datasetId,
      });

      const result = response.data?.result || {};

      const _curlCode = [
        result?.code?.curl_add_col || "",
        result?.code?.curl_add_row || "",
      ]
        .filter(Boolean)
        .map((code) => code.replace(/^\n+/, ""))
        .join("\n");

      const _typescriptCode = [
        result?.code?.typescript_add_col || "",
        result?.code?.typescript_add_row || "",
      ]
        .filter(Boolean)
        .map((code) => code.replace(/^\n+/, ""))
        .join("\n");

      const _pythonCode = [
        result?.code?.python_add_col || "",
        result?.code?.python_add_row || "",
      ]
        .filter(Boolean)
        .map((code) => code.replace(/^\n+/, ""))
        .join("\n");

      setValue("Pcode", result?.code?.python_add_row || "");
      setValue("Tcode", result?.code?.typescript_add_row || "");
      setValue("CUcode", result?.code?.curl_add_row || "");

      return response.data;
    },
    enabled: open, // Trigger query only when modal is open
  });

  const handleSubmit = () => {
    trackEvent(Events.rowFromSDKCreated, {
      [PropertyName.name]: data?.result?.dataset?.name,
      [PropertyName.languageUsed]: currentTab,
    });
    trackEvent(Events.addRowsSuccess, {
      [PropertyName.method]: "add rows via SDK or API",
    });
    onCloseClick();
  };

  const onCloseClick = () => {
    onClose();
    reset(); // Reset form values
    closeDrawer();
  };

  return (
    <Modal
      open={open}
      onClose={onCloseClick}
      sx={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        // p: "15px",
        overflow: "auto",
        "& .MuiBackdrop-root": { backgroundColor: "#00000040" },
      }}
    >
      <Box
        bgcolor="background.paper"
        mx="auto"
        maxWidth="919px"
        width="100%"
        borderRadius="16px"
        padding={2}
      >
        <Box display={"flex"} flexDirection={"column"} gap={0.5}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              variant="m2"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              Add Rows via SDK or API
            </Typography>
            <IconButton
              onClick={() => {
                setIsNext(false);
                onClose();
              }}
            >
              <Iconify icon="mdi:close" />
            </IconButton>
          </Box>
          <Box sx={{}}>
            <HelperText text="Datasets can be used to for evals, prompt experimentation, or fine-tuning." />
          </Box>
        </Box>
        {isFetching ? (
          <Box
            display="flex"
            justifyContent="center"
            alignItems="center"
            minHeight="200px"
            margin="25px 0"
          >
            <CircularProgress size={40} />
          </Box>
        ) : (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              listStyle: "none",
              margin: "25px 0",
            }}
          >
            <Box display="flex" alignItems="center">
              <Typography
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                variant="s1"
                component="span"
                width="141px"
              >
                Dataset Name:
              </Typography>
              <Box display="flex" alignItems="center" gap={1}>
                <Typography
                  bgcolor="background.neutral"
                  borderRadius="8px"
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  p="5px 12px"
                  color="text.primary"
                >
                  {data?.result?.dataset?.name || "N/A"}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() =>
                    copyToClipboard(data?.result?.dataset?.name, "Dataset Name")
                  }
                  sx={{ color: "text.secondary" }}
                >
                  <Iconify icon="eva:copy-outline" width={16} height={16} />
                </IconButton>
              </Box>
            </Box>
            <Box display="flex" alignItems="center">
              <Typography
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                variant="s1"
                component="span"
                width="141px"
              >
                Dataset ID:
              </Typography>
              <Box display="flex" alignItems="center" gap={1}>
                <Typography
                  bgcolor="background.neutral"
                  borderRadius="8px"
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  p="5px 12px"
                  color="text.primary"
                  sx={{
                    fontFamily: "monospace",
                    fontSize: "0.875rem",
                  }}
                >
                  {data?.result?.dataset?.id || "N/A"}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() =>
                    copyToClipboard(data?.result?.dataset?.id, "Dataset ID")
                  }
                  sx={{ color: "text.secondary" }}
                >
                  <Iconify icon="eva:copy-outline" width={16} height={16} />
                </IconButton>
              </Box>
            </Box>
            <Box display="flex" alignItems="center">
              <Typography
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                variant="s1"
                component="span"
                width="141px"
              >
                API Key:
              </Typography>
              <Box display="flex" alignItems="center" gap={1}>
                <Typography
                  bgcolor="background.neutral"
                  borderRadius="8px"
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  p="5px 12px"
                  color="text.primary"
                >
                  {data?.result?.apiKeys?.apiKey || "N/A"}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() =>
                    copyToClipboard(data?.result?.apiKeys?.apiKey, "API Key")
                  }
                  sx={{ color: "text.secondary" }}
                >
                  <Iconify icon="eva:copy-outline" width={16} height={16} />
                </IconButton>
              </Box>
            </Box>
            <Box display="flex" alignItems="center">
              <Typography
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                variant="s1"
                component="span"
                width="141px"
              >
                Secret Key:
              </Typography>
              <Box display="flex" alignItems="center" gap={1}>
                <Typography
                  bgcolor="background.neutral"
                  borderRadius="8px"
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  p="5px 12px"
                  color="text.primary"
                >
                  {data?.result?.apiKeys?.secretKey || "N/A"}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() =>
                    copyToClipboard(
                      data?.result?.apiKeys?.secretKey,
                      "Secret Key",
                    )
                  }
                  sx={{ color: "text.secondary" }}
                >
                  <Iconify icon="eva:copy-outline" width={16} height={16} />
                </IconButton>
              </Box>
            </Box>
          </Box>
        )}
        {!isFetching && (
          <Box>
            <TabWrapper>
              <CustomTabs
                textColor="primary"
                value={currentTab}
                onChange={(e, value) => setCurrentTab(value)}
                TabIndicatorProps={{
                  style: {
                    backgroundColor: theme.palette.primary.main,
                    opacity: 0.08,
                    height: "100%",
                    borderRadius: "8px",
                  },
                }}
              >
                {tabOptions.map((tab) => (
                  <CustomTab
                    key={tab.value}
                    label={tab.label}
                    value={tab.value}
                    disabled={tab.disabled}
                  />
                ))}
              </CustomTabs>
            </TabWrapper>
            <Box
              bgcolor="background.neutral"
              borderRadius="8px"
              border="1px solid var(--border-default)"
              p={2}
            >
              <ShowComponent condition={currentTab === "python"}>
                <FormCodeEditor
                  helperText={""}
                  readOnly
                  height="200px"
                  defaultLanguage="python"
                  control={control}
                  fieldName="Pcode"
                  options={editorOptions}
                />
              </ShowComponent>
              <ShowComponent condition={currentTab === "typescript"}>
                <FormCodeEditor
                  helperText={""}
                  readOnly
                  height="200px"
                  defaultLanguage="typescript"
                  control={control}
                  fieldName="Tcode"
                  options={editorOptions}
                />
              </ShowComponent>
              <ShowComponent condition={currentTab === "curl"}>
                <FormCodeEditor
                  helperText={""}
                  readOnly
                  height="200px"
                  defaultLanguage="bash"
                  control={control}
                  fieldName="CUcode"
                  options={editorOptions}
                />
              </ShowComponent>
            </Box>
          </Box>
        )}
        <Box
          sx={{
            marginTop: 4,
            display: " flex",
            justifyContent: "flex-end",
            gap: "12px",
          }}
        >
          <Button
            onClick={() => {
              setIsNext(false);
              onClose();
            }}
            variant="outlined"
          >
            <Typography
              variant="s2"
              fontWeight={"fontWeightSemiBold"}
              color="text.secondary"
            >
              Back
            </Typography>
          </Button>
          <LoadingButton
            onClick={handleSubmit}
            variant="contained"
            autoFocus
            color="primary"
          >
            <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
              Done
            </Typography>
          </LoadingButton>
        </Box>
      </Box>
    </Modal>
  );
};

AddSDKModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  closeDrawer: PropTypes.string,
};

export default AddSDKModal;
