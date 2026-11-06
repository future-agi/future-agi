import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import Image from "src/components/image";

const UploadedImageViewer = ({
  open,
  onClose,
  selectedImageId,
  removeImageBlot,
  replaceImageBlot,
}) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 960 } }}
      fullWidth
    >
      <DialogTitle sx={{ display: "flex", justifyContent: "space-between" }}>
        <Box>
          <Box>Uploaded Images</Box>
        </Box>
        <IconButton onClick={onClose} size="small">
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            width: "100%",
          }}
        >
          <Box
            sx={{
              border: "2px solid",
              borderColor: "divider",
              borderRadius: "4px",
              overflow: "hidden",
            }}
          >
            <Image src={selectedImageId?.src} height="400px" />
          </Box>
        </Box>
      </DialogContent>
      <DialogActions sx={{ justifyContent: "center" }}>
        <Button
          sx={{ minWidth: "250px" }}
          variant="outlined"
          onClick={() => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "image/*";
            input.multiple = true;
            input.onchange = (event) => {
              const files = event.target.files;
              if (files) {
                Array.from(files).forEach((file) => {
                  const reader = new FileReader();
                  reader.onload = (e) => {
                    replaceImageBlot(selectedImageId?.id, {
                      src: e.target.result,
                      alt: file.name,
                    });
                    onClose();
                  };
                  reader.readAsDataURL(file);
                });
              }
            };
            input.click();
          }}
        >
          Replace
        </Button>
        <Button
          sx={{ minWidth: "250px" }}
          variant="contained"
          color="error"
          onClick={() => {
            removeImageBlot(selectedImageId?.id);
            onClose();
          }}
        >
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
};

UploadedImageViewer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedImageId: PropTypes.object,
  removeImageBlot: PropTypes.func,
  replaceImageBlot: PropTypes.func,
};

export default UploadedImageViewer;
