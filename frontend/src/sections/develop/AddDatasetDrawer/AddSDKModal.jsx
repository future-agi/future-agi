import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
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
import { zodResolver } from "@hookform/resolvers/zod";
import { ManuallyCreateDatasetValidationSchema2 } from "./validation";
import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import { useNavigate } from "react-router";
import { CustomTab, CustomTabs, TabWrapper } from "./AddDatasetStyle";
import { ShowComponent } from "src/components/show";
import { FormCodeEditor } from "src/components/form-code-editor";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { getCreatedDatasetName } from "./sdkCreateDatasetResponse";

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

const AddSDKModal = ({ open, onClose, refreshGrid }) => {
  const theme = useTheme();
  const { control, handleSubmit, reset, setValue } = useForm({
    defaultValues,
    resolver: zodResolver(ManuallyCreateDatasetValidationSchema2),
  });
  const [isNext, setIsNext] = useState(false);
  const [currentTab, setCurrentTab] = useState("python");
  const navigate = useNavigate();

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

  const {
    mutate: addRowSDK,
    isPending,
    data,
  } = useMutation({
    mutationFn: (data) => axios.post(endpoints.row.addRowSdk, data, {}),
    onSuccess: (data) => {
      const result = data?.data?.result;
      setValue("Pcode", result?.code?.python_add_col || "");
      setValue("Tcode", result?.code?.typescript_add_col || "");
      setValue("CUcode", result?.code?.curl_add_col || "");
    },
    onError: (error) => {
      setIsNext(false);
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to generate SDK snippets", {
          retryAction: "generating SDK snippets",
        }),
        { variant: "error" },
      );
    },
  });

  const { mutate: createEmptyDataset, isPending: isPending1 } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.createEmptyDataset, data, {
        headers: { "Content-Type": "multipart/form-data" },
      }),
    onSuccess: (data) => {
      onCloseClick();
      //@ts-ignore
      addRowSDK({
        dataset_name: getCreatedDatasetName(data),
      });
      setIsNext(true);
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create dataset", {
          retryAction: "creating this dataset",
        }),
        { variant: "error" },
      );
    },
  });

  const onSubmit = (data) => {
    trackEvent(Events.dataAddSuccessfull, {
      [PropertyName.method]: "add from sdk",
    });
    //@ts-ignore
    createEmptyDataset({
      new_dataset_name: data?.name,
      model_type: "GenerativeLLM",
      is_sdk: true,
    });
  };
  const onCloseClick = () => {
    onClose();
    reset();
    refreshGrid();
  };

  return (
    <>
      <Dialog open={open} onClose={onCloseClick} maxWidth="sm" PaperProps={{}}>
        <Box sx={{ padding: 2 }}>
          <DialogTitle sx={{ padding: 0, margin: 0 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                // minWidth: "152px",
              }}
            >
              <Typography
                fontWeight={"fontWeightMedium"}
                color="text.primary"
                variant="m2"
              >
                Create Dataset
              </Typography>
              <IconButton onClick={onCloseClick}>
                <Iconify icon="mdi:close" color="text.primary" />
              </IconButton>
            </Box>
            <HelperText text="Datasets can be used to for evals, prompt experimentation, or fine - tuning." />
          </DialogTitle>
          <DialogContent sx={{ padding: 0 }}>
            <Box sx={{ paddingTop: "20px" }}>
              <FormTextFieldV2
                fieldName="name"
                placeholder="Enter dataset name"
                control={control}
                label="Dataset Name"
                size="small"
                fullWidth
                rules={{ required: "Name is required" }}
              />
            </Box>
          </DialogContent>
          <DialogActions sx={{ padding: 0, paddingTop: 4 }}>
            <Box display={"flex"} gap={"12px"}>
              <Button
                onClick={onCloseClick}
                variant="outlined"
                // sx={{ padding: "6px 24px", borderColor: "divider" }}
              >
                {/* <Typography variant="s2" fontWeight={"fontWeightSemiBold"} color="text.secondary">
                  </Typography> */}
                Cancel
              </Button>
              <LoadingButton
                onClick={handleSubmit(onSubmit)}
                variant="contained"
                autoFocus
                color="primary"
                loading={isPending1}
                // sx={{ padding: "6px 24px" }}
              >
                {/* <Typography variant="s2" fontWeight={"fontWeightSemiBold"}> */}
                Next
                {/* </Typography> */}
              </LoadingButton>
            </Box>
          </DialogActions>
        </Box>
      </Dialog>

      <Modal
        open={isNext}
        onClose={() => {
          setIsNext(false);
          navigate(
            `/dashboard/develop/${data?.data?.result?.dataset?.id}?tab=data`,
          );
        }}
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
                  navigate(
                    `/dashboard/develop/${data?.data?.result?.dataset?.id}?tab=data`,
                  );
                }}
              >
                <Iconify icon="mdi:close" />
              </IconButton>
            </Box>
            <Box sx={{}}>
              <HelperText text="Datasets can be used to for evals, prompt experimentation, or fine-tuning." />
            </Box>
          </Box>
          {isPending ? (
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
                    color="black"
                  >
                    {data?.data?.result?.dataset?.name}
                  </Typography>
                  <IconButton
                    size="small"
                    onClick={() =>
                      copyToClipboard(
                        data?.data?.result?.dataset?.name,
                        "Dataset Name",
                      )
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
                    color="black"
                    sx={{
                      fontFamily: "monospace",
                      fontSize: "0.875rem",
                    }}
                  >
                    {data?.data?.result?.dataset?.id}
                  </Typography>
                  <IconButton
                    size="small"
                    onClick={() =>
                      copyToClipboard(
                        data?.data?.result?.dataset?.id,
                        "Dataset ID",
                      )
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
                    color="black"
                  >
                    {data?.data?.result?.apiKeys?.apiKey}
                  </Typography>
                  <IconButton
                    size="small"
                    onClick={() =>
                      copyToClipboard(
                        data?.data?.result?.apiKeys?.apiKey,
                        "API Key",
                      )
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
                    color="black"
                  >
                    {data?.data?.result?.apiKeys?.secretKey}
                  </Typography>
                  <IconButton
                    size="small"
                    onClick={() =>
                      copyToClipboard(
                        data?.data?.result?.apiKeys?.secretKey,
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
          {!isPending && (
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
            <LoadingButton
              onClick={() => {
                trackEvent(Events.datasetFromSDKCreated, {
                  [PropertyName.name]: data?.data?.result?.dataset?.name,
                  [PropertyName.languageUsed]: currentTab,
                });
                setIsNext(false);
                navigate(
                  `/dashboard/develop/${data?.data?.result?.dataset?.id}?tab=data`,
                );
              }}
              variant="contained"
              autoFocus
              color="primary"
              loading={isPending}
            >
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                Done
              </Typography>
            </LoadingButton>
          </Box>
        </Box>
      </Modal>
    </>
  );
};

AddSDKModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default AddSDKModal;
