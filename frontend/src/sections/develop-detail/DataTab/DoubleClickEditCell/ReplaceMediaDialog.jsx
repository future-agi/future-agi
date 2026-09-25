import React, { useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../../components/show";
import { useForm } from "react-hook-form";
import { LoadingButton } from "@mui/lab";
import _ from "lodash";
import { RHFUpload } from "src/components/hook-form";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "../../../../components/FormTextField/FormTextFieldV2";
import { enqueueSnackbar } from "notistack";
import { getFileIcon } from "../../../knowledge-base/sheet-view/icons";
import {
  isDocumentWebAddress,
  NOT_A_WEB_ADDRESS_MESSAGE,
} from "./editHelper";

const tabItems = [
  { label: "Upload", value: "upload" },
  { label: "Link", value: "link" },
];

const titleMapper = {
  replace: "Replace File",
  upload: "Upload File",
};
const MAX_FILE_SIZE = 1024 * 1024 * 1024; // 1GB

export default function ReplaceMediaDialog({
  open,
  onClose,
  type = "replace",
  onUpload,
}) {
  const description = `Upload  a File from your system`;
  const theme = useTheme();

  const [currentTab, setCurrentTab] = useState("upload");
  const { control, reset, handleSubmit, watch, setValue } = useForm({
    defaultValues: {
      file: [],
      link: [],
    },
  });

  const watchedFiles = watch("file");

  const handleFileChange = (acceptedFiles) => {
    const allowedTypes = [
      "application/pdf",
      "application/msword",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/plain",
    ];

    let hasError = false;
    let validFile = null;

    for (const file of acceptedFiles) {
      if (!allowedTypes.includes(file.type)) {
        enqueueSnackbar(`Invalid file type: ${file.name}`, {
          variant: "error",
        });
        hasError = true;
        continue;
      }
      if (file.size > MAX_FILE_SIZE) {
        enqueueSnackbar(`File too large: ${file.name}`, { variant: "error" });
        hasError = true;
        continue;
      }
      validFile = file; // only take the first valid file
      break;
    }

    if (validFile) {
      setValue("file", [validFile], { shouldValidate: true });
    } else if (hasError) {
      setValue("file", [], { shouldValidate: true });
    }
  };

  const handleClose = () => {
    onClose();
    reset();
  };

  const onSubmit = (data) => {
    if (currentTab === "upload") {
      onUpload(data?.file?.[0]);
      handleClose();
      return;
    }
    const link = String(data?.link ?? "").trim();
    if (!isDocumentWebAddress(link)) {
      enqueueSnackbar(NOT_A_WEB_ADDRESS_MESSAGE, { variant: "error" });
      return;
    }
    onUpload(link);
    handleClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      sx={{}}
      PaperProps={{
        sx: {
          minWidth: "500px",
          minHeight: "300px",
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        },
      }}
    >
      <DialogTitle
        sx={{
          p: 0,
        }}
      >
        <Stack>
          <Stack
            direction={"row"}
            alignItems={"center"}
            justifyContent={"space-between"}
          >
            <Typography
              variant="m3"
              color={"text.primary"}
              fontWeight={"fontWeightSemiBold"}
            >
              {titleMapper[type]}
            </Typography>
            <IconButton size="small" onClick={handleClose}>
              <SvgColor
                sx={{
                  height: 24,
                  width: 24,
                  bgcolor: "text.primary",
                }}
                src="/assets/icons/ic_close.svg"
              />
            </IconButton>
          </Stack>
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            {description}
          </Typography>
        </Stack>
      </DialogTitle>
      <DialogContent
        sx={{
          p: 0,
        }}
      >
        <Box
          sx={{
            mb: theme.spacing(2),
          }}
        >
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
        <ShowComponent condition={currentTab === "upload"}>
          {watchedFiles && watchedFiles.length > 0 ? (
            <Stack
              sx={{
                padding: theme.spacing(3),
                border: "1px dashed var(--border-default)",
                borderRadius: theme.spacing(1),
              }}
              direction={"row"}
              justifyContent={"space-between"}
            >
              <Stack direction={"row"} gap={theme.spacing(1.5)}>
                <Box
                  component={"img"}
                  sx={{
                    height: "20px",
                    width: "20px",
                  }}
                  alt="document icon"
                  src={getFileIcon(
                    watchedFiles?.[0]?.name.split(".").pop().toLowerCase(),
                  )}
                />
                <Stack>
                  <Typography
                    variant="s2"
                    color={"text.primary"}
                    fontWeight={"fontWeightMedium"}
                  >
                    {watchedFiles?.[0]?.name}
                  </Typography>
                  <Typography
                    variant="s3"
                    color={"text.disabled"}
                    fontWeight={"fontWeightRegular"}
                  >
                    {watchedFiles?.[0]?.name.split(".").pop()}
                  </Typography>
                </Stack>
              </Stack>
              <LoadingButton
                size="small"
                startIcon={
                  <SvgColor
                    src={`/assets/icons/components/ic_delete.svg`}
                    sx={{ width: 16, height: 16, color: "text.primary" }}
                  />
                }
                onClick={() => reset({ file: [] })}
                sx={{
                  color: "text.primary",
                  fontSize: "12px",
                  fontWeight: 400,
                  lineHeight: "18px",
                  ml: "auto",
                }}
              >
                Delete
              </LoadingButton>
            </Stack>
          ) : (
            <RHFUpload
              control={control}
              showDropRejection={false}
              name="file"
              hidePreview={true}
              uploadIcon={
                <Iconify
                  icon="solar:download-minimalistic-bold"
                  height={24}
                  width={24}
                  color="primary.main"
                />
              }
              heading="Choose a file or drag & drop it here"
              description="PDF, DOCX, DOC, TXT up to 1GB"
              actionButton={
                <Button
                  variant="outlined"
                  size="small"
                  sx={{
                    paddingY: (theme) => theme.spacing(0.75),
                    paddingX: (theme) => theme.spacing(3),
                    borderRadius: (theme) => theme.spacing(1),
                    background: (theme) => theme.palette.background.paper,
                    color: "text.primary",
                    borderColor: "text.disabled",
                  }}
                >
                  Browse files
                </Button>
              }
              multiple={true}
              showIllustration={false}
              accept={{
                "application/pdf": [".pdf"],
                "application/msword": [".doc"],
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                  [".docx"],
                "text/plain": [".txt"],
              }}
              sx={{ paddingY: (theme) => theme.spacing(3) }}
              onDrop={handleFileChange}
            />
          )}
        </ShowComponent>
        <ShowComponent condition={currentTab === "link"}>
          <FormTextFieldV2
            control={control}
            label="Link"
            fieldName="link"
            placeholder="https://www.google.com"
            fullWidth
            size="small"
          />
        </ShowComponent>
      </DialogContent>
      <DialogActions
        sx={{
          mt: theme.spacing(2),
          p: 0,
        }}
      >
        <LoadingButton
          color="primary"
          variant="contained"
          startIcon={<SvgColor src="/icons/datasets/upload_file.svg" />}
          onClick={handleSubmit(onSubmit)}
          sx={{
            minWidth: "200px",
          }}
          disabled={
            (currentTab === "upload" &&
              (!watchedFiles || watchedFiles.length === 0)) ||
            (currentTab === "link" && !String(watch("link") || "").trim())
          }
        >
          Upload
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

ReplaceMediaDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  type: PropTypes.string,
  onUpload: PropTypes.func,
};
