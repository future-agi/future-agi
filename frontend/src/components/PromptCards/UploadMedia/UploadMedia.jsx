import {
  Box,
  Button,
  Drawer,
  IconButton,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useForm, useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import UploadFile from "./UploadFile";
import UploadLink from "./UploadLink";
import UploadPreview from "./UploadPreview";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";

const tabItems = [
  { label: "Upload", value: "upload" },
  { label: "Link", value: "link" },
];

const TitleMap = {
  image: "Add image",
  audio: "Add audio",
  pdf: "Add pdf",
};

const DescriptionMap = {
  image: "Upload multiple images",
  audio: "Upload multiple audios",
  pdf: "Upload multiple pdfs",
};

const UploadMediaChild = ({ control, type, onClose, onSubmit, isPending }) => {
  const theme = useTheme();

  const [currentTab, setCurrentTab] = useState("upload");

  const links = useWatch({ control, name: "links" });
  const files = useWatch({ control, name: "files" });

  const isDisabled = links.length === 0 && files.length === 0;

  return (
    <Box
      sx={{
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex" }}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Typography
            fontWeight={"fontWeightMedium"}
            color="text.primary"
            variant="m3"
          >
            {TitleMap[type]}
          </Typography>
          <Typography variant="s1" color="text.secondary">
            {DescriptionMap[type]}
          </Typography>
        </Box>
        <IconButton
          onClick={onClose}
          sx={{ position: "absolute", top: "10px", right: "12px" }}
        >
          <Iconify icon="mingcute:close-line" color="text.primary" />
        </IconButton>
      </Box>
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
      <ShowComponent condition={currentTab === "upload"}>
        <UploadFile control={control} type={type} />
      </ShowComponent>
      <ShowComponent condition={currentTab === "link"}>
        <UploadLink control={control} />
      </ShowComponent>
      <UploadPreview control={control} type={type} />
      <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
        <LoadingButton
          loading={isPending}
          variant="contained"
          color="primary"
          sx={{ minWidth: "200px" }}
          onClick={onSubmit}
          disabled={isDisabled}
        >
          Save
        </LoadingButton>
      </Box>
    </Box>
  );
};

UploadMediaChild.propTypes = {
  control: PropTypes.object.isRequired,
  type: PropTypes.oneOf(["image", "audio"]),
  onClose: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  isPending: PropTypes.bool,
};

const UploadMedia = ({ open, onClose, type, handleEmbedMedia }) => {
  const [isConfirmDialogOpen, setIsConfirmDialogOpen] = useState(false);
  const { id } = useParams();
  const { control, reset, formState, handleSubmit } = useForm({
    defaultValues: {
      files: [],
      links: [],
    },
  });

  const handleClose = () => {
    reset();
    onClose();
    setIsConfirmDialogOpen(false);
  };

  const handleConfirmClose = (override = false) => {
    if (formState.isDirty && !override) {
      setIsConfirmDialogOpen(true);
    } else {
      handleClose();
    }
  };

  const { mutate: uploadFile, isPending } = useMutation({
    mutationFn: (data) => {
      const formData = new FormData();
      data.files.forEach((file) => {
        formData.append("files", file?.file);
      });

      data.links.forEach((link) => {
        formData.append("links", link.url);
      });

      formData.append("type", type);
      return axios.post(endpoints.misc.uploadFile, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });
    },
    onSuccess: (data, variables) => {
      const uploadedUrl = data?.data?.result || [];
      const filesOrLinks = variables?.files || variables?.links || [];
      const mappedMediaData = uploadedUrl.reduce((acc, data, index) => {
        if (!data.url) return acc;
        if (type === "image") {
          acc.push({
            url: data.url,
            img_name: data?.fileName ?? filesOrLinks[index]?.name,
            img_size: filesOrLinks[index]?.size,
          });
        }
        if (type === "audio") {
          acc.push({
            url: data.url,
            audio_name: data?.fileName ?? filesOrLinks[index]?.name,
            audio_size: filesOrLinks[index]?.size,
            audio_type: filesOrLinks[index]?.type,
          });
        }
        if (type === "pdf") {
          acc.push({
            url: data.url,
            pdf_name: data?.fileName ?? filesOrLinks[index]?.name,
            pdf_size: filesOrLinks[index]?.size,
          });
        }
        return acc;
      }, []);
      const isError = uploadedUrl.some((url) => url.error);
      trackEvent(Events.promptSavemediaClicked, {
        [PropertyName.promptId]: id,
        type: type,
        size: filesOrLinks?.map((item) => item?.size),
        status: uploadedUrl?.map((item) => (item?.error ? "error" : "success")),
      });
      if (isError) {
        const errors = uploadedUrl?.map((url) => url?.error)?.join(",");
        enqueueSnackbar(errors, { variant: "error" });
      } else {
        handleClose();
        handleEmbedMedia(type, mappedMediaData);
      }
    },
  });

  const onFormSubmit = (data) => {
    uploadFile(data);
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleConfirmClose}
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
      <UploadMediaChild
        onClose={handleConfirmClose}
        control={control}
        type={type}
        onSubmit={handleSubmit(onFormSubmit)}
        isPending={isPending}
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
    </Drawer>
  );
};

UploadMedia.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  type: PropTypes.oneOf(["image", "audio"]),
  handleEmbedMedia: PropTypes.func,
};

export default UploadMedia;
