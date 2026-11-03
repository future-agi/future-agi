import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Button, Dialog, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import Image from "src/components/image";
import { ShowComponent } from "src/components/show";
import { formatFileSize } from "src/utils/utils";
import { useSnackbar } from "src/components/snackbar";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getRandomId } from "src/utils/utils";
import { LoadingButton } from "@mui/lab";

const ViewReplaceImage = ({
  open,
  onClose,
  selectedImage,
  onImageDelete,
  onImageReplace,
}) => {
  const fileInputRef = useRef(null);

  const { enqueueSnackbar } = useSnackbar();

  const { mutate: uploadFile, isPending } = useMutation({
    mutationFn: (data) => {
      const formData = new FormData();

      data.files.forEach((file) => {
        formData.append("files", file?.file);
      });

      formData.append("type", "image");
      return axios.post(endpoints.misc.uploadFile, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });
    },
    onSuccess: (data, variables) => {
      onClose();
      const uploadedUrl = data?.data?.result || [];
      const files = variables?.files || [];
      onImageReplace(selectedImage.id, {
        url: uploadedUrl[0]?.url,
        img_name: files[0]?.name,
        img_size: files[0]?.size,
      });
    },
  });

  const handleReplaceClick = () => {
    fileInputRef.current?.click();
  };

  useEffect(() => {
    if (selectedImage?.replace) {
      setTimeout(() => {
        if (fileInputRef.current) {
          handleReplaceClick();
        }
      }, 0);
    }
  }, [selectedImage?.replace]);

  const handleFileChange = (event) => {
    const file = event.target.files?.[0];
    const maxSize = 5 * 1024 * 1024; // 5MB

    if (file) {
      if (file.size > maxSize) {
        enqueueSnackbar("File size is too large", {
          variant: "error",
        });
        return;
      }
      const previewUrl = URL.createObjectURL(file);
      uploadFile({
        files: [
          {
            id: getRandomId(),
            file: file,
            name: file.name,
            size: file.size,
            previewUrl: previewUrl,
          },
        ],
        links: [],
      });
    }
    // Reset the input value so the same file can be selected again
    event.target.value = "";
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
    >
      <Box
        sx={{
          p: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Box sx={{ display: "flex", justifyContent: "space-between" }}>
          <Typography variant="m3" fontWeight="fontWeightSemiBold">
            View Image
          </Typography>
          <IconButton
            onClick={onClose}
            sx={{ position: "absolute", top: "10px", right: "12px" }}
          >
            <Iconify icon="solar:close-linear" color="text.primary" />
          </IconButton>
        </Box>
        <Box
          sx={{
            padding: "12px",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <Image
            src={selectedImage?.url}
            alt={selectedImage?.name}
            height="500px"
          />
          <Box sx={{ display: "flex", flexDirection: "column" }}>
            <ShowComponent condition={selectedImage?.name}>
              <Typography variant="s1" fontWeight="medium">
                {selectedImage?.name}
              </Typography>
            </ShowComponent>
            <ShowComponent condition={selectedImage?.size}>
              <Typography variant="s2" color="text.disabled">
                {formatFileSize(selectedImage?.size)}
              </Typography>
            </ShowComponent>
          </Box>
        </Box>
        <Box sx={{ display: "flex", gap: 2, justifyContent: "center" }}>
          <input
            type="file"
            ref={fileInputRef}
            style={{ display: "none" }}
            onChange={handleFileChange}
            accept="image/jpeg,image/jpg,image/png,image/gif,image/webp"
          />
          <LoadingButton
            variant="outlined"
            loading={isPending}
            sx={{
              width: "200px",
              color: "text.disabled",
            }}
            onClick={handleReplaceClick}
          >
            Replace
          </LoadingButton>
          <Button
            variant="contained"
            color="error"
            sx={{ width: "200px" }}
            onClick={() => {
              onImageDelete(selectedImage.id);
              onClose();
            }}
          >
            Delete
          </Button>
        </Box>
      </Box>
    </Dialog>
  );
};

ViewReplaceImage.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedImage: PropTypes.object,
  onImageDelete: PropTypes.func,
  onImageReplace: PropTypes.func,
};

export default ViewReplaceImage;
