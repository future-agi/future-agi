import {
  Box,
  Button,
  Drawer,
  IconButton,
  Tab,
  Tabs,
  Typography,
  useTheme,
  Link,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import UploadKnowledgeBaseFile from "./UploadKnowledgeBaseFile";
import UploadKnowledgeBaseSDK from "./UploadKnowledgeBaseSDK";
import SingleFileDetail from "./SingleFileDetail";
import { getFileExtension } from "src/utils/utils";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router";
import SdkInfoDialog from "./SdkInfoDialog";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useDebounce } from "src/hooks/use-debounce";
import { RESPONSE_CODES } from "src/utils/constants";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useBeforeUnload } from "src/hooks/useBeforeUnload";

const tabItems = [
  { label: "Upload", value: "Upload", disabled: false },
  {
    label: "Import from SDK",
    value: "ImprortfromSDK",
    disabled: false,
  },
  // {
  //   label: "Sync from integration",
  //   value: "SyncFromIntegration",
  //   disabled: true,
  // },
  // { label: "Link", value: "Link", disabled: true },
];

const CreateKnowledgeBaseDrawerChild = ({
  setHasData,
  refreshGrid = null,
  setValue,
  handleSubmit,
  isConfirmDialogOpen,
  setIsConfirmDialogOpen,
  files,
  setError,
  control,
  knowledgeId,
  handleConfirmCLose,
  handleClose,
  name,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [currentTab, setCurrentTab] = useState("Upload");
  const { refetch: refetchKnowledgeBaseList } = useKnowledgeBaseList();

  const [showSdkInfo, setShowSdkInfo] = useState(false);

  const { mutate: handleDelete, isPending: loadingDeleteFile } = useMutation({
    mutationFn: (body) =>
      axios.delete(`${endpoints.knowledge.knowledgeBase}`, {
        data: { kb_ids: body?.ids ? [body?.ids] : [] },
      }),
    onSuccess: (data, variable) => {
      deleteFile(variable?.index, "");
    },
    onError: () => {},
  });

  const deleteFile = (index, status) => {
    if (status === "success") {
      // @ts-ignore
      handleDelete({ ids: files?.file[index]?.id, index });
    } else {
      setValue("file", {
        file: files?.file?.filter((_, ind) => index !== ind),
      });
    }
  };

  const { mutate: createKnowledgeBase, isPending } = useMutation({
    mutationFn: (body) => {
      if (knowledgeId) {
        return axios.patch(`${endpoints.knowledge.knowledgeBase}`, body, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      } else {
        return axios.post(`${endpoints.knowledge.knowledgeBase}`, body, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      }
    },
    onSuccess: (data) => {
      refetchKnowledgeBaseList();
      const id = data?.data?.result?.kbId;
      if (!knowledgeId && id) {
        setHasData?.(true);
        enqueueSnackbar(
          data?.data?.result?.detail || "Creating Knowledge Base",
          { variant: "success" },
        );
        handleClose();
        navigate(`/dashboard/knowledge/${id}`);
      } else if (knowledgeId) {
        enqueueSnackbar("Files added successfully", { variant: "success" });
        refreshGrid();
        handleClose();
      } else {
        enqueueSnackbar("Something went wrong", { variant: "error" });
      }
    },
    meta: {
      errorHandled: true,
    },
    onError: (data) => {
      if (data?.statusCode == RESPONSE_CODES.LIMIT_REACHED) return;

      // Handle file upload errors - update file statuses and don't show generic error
      if (data?.result?.notUploaded) {
        const file = data?.result?.files ?? [];
        setValue("file", {
          file: files.file.map((item) => {
            // find name from response
            const result = file?.find((fi) => fi?.name === item?.item?.name);
            return {
              ...item,
              ...(result?.name === item?.item?.name
                ? {
                    status: "error",
                    statusReason: result?.error,
                  }
                : {
                    status: "success",
                  }),
            };
          }),
        });
        return;
      }

      // Handle name uniqueness error
      if (
        typeof data?.result === "string" &&
        data?.result?.includes("name must be unique")
      ) {
        setError(
          "name",
          { type: "focus", message: data?.result },
          { shouldFocus: true },
        );
      } else {
        // Generic error
        const message =
          typeof data?.result === "string"
            ? data?.result
            : data?.result?.message || "An unexpected error occurred.";
        enqueueSnackbar(message, {
          variant: "error",
        });
      }
    },
  });

  const onCreate = (data) => {
    const formData = new FormData();
    formData.append("name", data?.name);

    data.file.file.forEach((file) => {
      formData.append("file", file.item);
    });
    if (knowledgeId) {
      formData.append("kb_id", knowledgeId);
    }
    //@ts-ignore
    createKnowledgeBase(formData);
  };

  const handleCloseSdkInfo = () => {
    setShowSdkInfo(false);
  };

  const handleShowSdkInfo = () => {
    setShowSdkInfo(true);
  };

  const handleOpenSDKTab = () => {
    setCurrentTab("ImprortfromSDK");
    handleCloseSdkInfo();
  };

  // show leave site warning when files are being uploaded
  useBeforeUnload(isPending);

  const debouncedKbName = useDebounce(name.trim(), 300);

  const { data: sdkCode } = useQuery({
    queryKey: ["kb-import-from-sdk", knowledgeId, debouncedKbName],
    queryFn: () =>
      axios.get(endpoints.knowledge.knowledgeBase, {
        params: {
          type: knowledgeId ? "update" : "create",
          name: !knowledgeId ? debouncedKbName : name,
          kb_id: knowledgeId ?? "",
        },
      }),
    select: (d) => d?.data?.result,
  });

  return (
    <>
      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          padding: 2,
          justifyContent: "space-between",
        }}
        component="form"
      >
        <Box
          sx={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
        >
          <Box sx={{ display: "flex" }}>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: "2px",
                width: "92%",
              }}
            >
              <Box
                display="flex"
                alignItems="center"
                justifyContent="space-between"
              >
                <Typography
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                  typography="m3"
                >
                  {knowledgeId ? "Add files" : "Create knowledge base"}
                </Typography>
                <Link
                  href="https://docs.futureagi.com/docs/knowledge-base"
                  underline="always"
                  color="blue.500"
                  target="_blank"
                  rel="noopener noreferrer"
                  fontWeight="fontWeightMedium"
                >
                  Learn more
                </Link>
              </Box>

              <HelperText
                text={
                  knowledgeId
                    ? "Add more files to update your knowledge base"
                    : "You can update the files in the background or discard the changes."
                }
              />
            </Box>
            <IconButton
              disabled={isPending}
              onClick={handleConfirmCLose}
              sx={{ position: "absolute", top: "10px", right: "12px" }}
            >
              <Iconify icon="mingcute:close-line" color="text.primary" />
            </IconButton>
          </Box>

          <Box
            sx={{
              py: "16px",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
            }}
          >
            <Box sx={{ display: "flex", gap: "16px", flexDirection: "column" }}>
              {!knowledgeId && (
                // All fields Here
                <FormTextFieldV2
                  control={control}
                  fieldName="name"
                  label="Name"
                  fullWidth
                  placeholder="Name"
                  size="small"
                  sx={{
                    "& input:-webkit-autofill": {
                      backgroundColor: "transparent !important",
                      WebkitBoxShadow:
                        "0 0 0px 1000px var(--bg-paper, white) inset !important",
                      WebkitTextFillColor: "var(--text-primary) !important",
                    },
                    "& input:focus": {
                      outline: "none",
                      backgroundColor: "background.paper !important",
                    },
                  }}
                />
              )}

              <Box>
                <Tabs
                  textColor="primary"
                  value={currentTab}
                  onChange={(e, value) => {
                    setCurrentTab(value);
                  }}
                  TabIndicatorProps={{
                    style: { backgroundColor: theme.palette.primary.main },
                  }}
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                >
                  {tabItems.map((tab) => (
                    <Tab
                      disabled={tab.disabled}
                      key={tab.value}
                      label={tab.label}
                      value={tab.value}
                      sx={{
                        paddingLeft: "24px",
                        paddingRight: "24px",
                        ...theme.typography["s1"],
                        fontWeight: theme.typography["fontWeightSemiBold"],
                        "&:not(.Mui-selected)": {
                          color: "text.disabled", // Color for unselected tabs
                          fontWeight: theme.typography["fontWeightMedium"],
                        },
                        ["&:not(:last-of-type)"]: {
                          marginRight: "4px",
                        },
                      }}
                    />
                  ))}
                </Tabs>
              </Box>
            </Box>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <Box>
                {currentTab === "Upload" && (
                  <UploadKnowledgeBaseFile
                    isPending={isPending}
                    handleShowSdkInfo={handleShowSdkInfo}
                    control={control}
                  />
                )}
                {currentTab === "ImprortfromSDK" && (
                  <UploadKnowledgeBaseSDK
                    pythonCode={sdkCode?.code}
                    action={knowledgeId ? "update" : "add"}
                  />
                )}
              </Box>
              {currentTab === "Upload" && files?.file?.length > 0 && (
                <Typography
                  typography="s2"
                  fontWeight={"fontWeightMedium"}
                  color="text.disabled"
                >
                  Files uploaded:{" "}
                  {files?.file?.length -
                    (files?.file?.filter((file) => file?.status === "error")
                      ?.length || 0)}
                  /{files?.file?.length}
                </Typography>
              )}
            </Box>
            <Box
              sx={{
                flex: 1,
                flexgrow: 1,
                height: "100%",
                overflow: "auto",
                gap: "12px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {/* here is the uploading files UI  */}
              {currentTab === "Upload" &&
                files?.file
                  ?.sort((a, b) => {
                    const statusOrder = {
                      error: 0,
                      not_started: 1,
                      success: 2,
                    };
                    return statusOrder[a.status] - statusOrder[b.status];
                  })
                  ?.map((file, index) => {
                    const fileSizeInBytes = file.item.size;
                    let fileSize;
                    if (fileSizeInBytes < 1024) {
                      fileSize = `${fileSizeInBytes} Bytes`; // Less than 1 KB
                    } else if (fileSizeInBytes < 1024 * 1024) {
                      fileSize = `${(fileSizeInBytes / 1024).toFixed(2)} KB`; // Less than 1 MB
                    } else {
                      fileSize = `${(fileSizeInBytes / (1024 * 1024)).toFixed(2)} MB`; // 1 MB or more
                    }
                    const fileType = getFileExtension(file.item.type);

                    return (
                      <SingleFileDetail
                        isLoading={loadingDeleteFile || isPending}
                        key={index}
                        file={file}
                        fileSize={fileSize}
                        deleteFile={(status) => deleteFile(index, status)}
                        fileType={fileType}
                      />
                    );
                  })}
            </Box>
          </Box>
        </Box>
        {currentTab === "Upload" && (
          <Box marginTop={"auto"}>
            <LoadingButton
              disabled={
                !files?.file?.length ||
                files?.file?.some((item) => item.status === "error")
              }
              loading={isPending}
              fullWidth
              type="button"
              variant="contained"
              color="primary"
              onClick={handleSubmit(onCreate)}
              sx={{
                py: "8px",
              }}
            >
              <Typography typography="s1" fontWeight={"fontWeightSemiBold"}>
                {knowledgeId ? "Add" : "Create"}
              </Typography>
            </LoadingButton>
          </Box>
        )}
      </Box>
      <SdkInfoDialog
        onSubmit={handleOpenSDKTab}
        open={showSdkInfo}
        onClose={handleCloseSdkInfo}
      />
      <ConfirmDialog
        content="Are you sure you want to close? Your work will be lost"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={handleClose}
          >
            Confirm
          </Button>
        }
        open={isConfirmDialogOpen}
        onClose={() => setIsConfirmDialogOpen(false)}
        title="Confirm Action"
        message="Are you sure you want to close?"
      />
    </>
  );
};

CreateKnowledgeBaseDrawerChild.propTypes = {
  setHasData: PropTypes.func,
  refreshGrid: PropTypes.func,
  setValue: PropTypes.func,
  handleSubmit: PropTypes.func,
  setError: PropTypes.func,
  files: PropTypes.array,
  isConfirmDialogOpen: PropTypes.bool,
  setIsConfirmDialogOpen: PropTypes.func,
  control: PropTypes.any,
  knowledgeId: PropTypes.string,
  handleConfirmCLose: PropTypes.func,
  handleClose: PropTypes.func,
  name: PropTypes.string,
};

const CreateKnowledgeBaseDrawer = ({
  open,
  onClose,
  setHasData,
  refreshGrid,
  data,
}) => {
  const { knowledgeId } = useParams();
  const {
    control,
    watch,
    reset,
    setValue,
    handleSubmit,
    setError,
    formState: { isDirty },
  } = useForm({
    defaultValues: { name: data?.name || "", file: null },
  });

  useEffect(() => {
    if (data?.name) {
      setValue("name", data.name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.name]);

  const files = watch("file");
  const name = watch("name");

  const [isConfirmDialogOpen, setIsConfirmDialogOpen] = useState(false);

  const handleClose = () => {
    reset();
    onClose();
    setIsConfirmDialogOpen(false);
  };

  const handleConfirmCLose = () => {
    // show confirm close if the form is dirty
    if (isDirty) {
      setIsConfirmDialogOpen(true);
      // else close drawer
    } else {
      handleClose();
    }
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleConfirmCLose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          width: "570px",
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
      <CreateKnowledgeBaseDrawerChild
        setHasData={setHasData}
        refreshGrid={refreshGrid}
        handleSubmit={handleSubmit}
        setValue={setValue}
        setError={setError}
        files={files}
        name={name}
        setIsConfirmDialogOpen={setIsConfirmDialogOpen}
        isConfirmDialogOpen={isConfirmDialogOpen}
        control={control}
        knowledgeId={knowledgeId}
        handleConfirmCLose={handleConfirmCLose}
        handleClose={handleClose}
      />
    </Drawer>
  );
};

export default CreateKnowledgeBaseDrawer;

CreateKnowledgeBaseDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  setHasData: PropTypes.func,
  refreshGrid: PropTypes.func,
  data: PropTypes.object,
};
