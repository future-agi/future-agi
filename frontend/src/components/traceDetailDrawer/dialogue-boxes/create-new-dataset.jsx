import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  Typography,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const CreateNewDataset = ({ open, onClose }) => {
  return (
    <Box>
      <Dialog
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            width: "40%",
            maxWidth: "70%",
          },
        }}
      >
        <IconButton
          onClick={onClose}
          sx={{
            position: "absolute",
            top: 20,
            right: 10,
            color: "grey",
            zIndex: 1,
          }}
        >
          <Iconify icon="solar:close-linear" width={24} />
        </IconButton>
        <DialogTitle sx={{ fontWeight: "600" }}>Create Dataset</DialogTitle>
        <DialogContent>
          <Typography
            variant="subtitle1"
            sx={{
              marginBottom: 2,
              color: "grey",
              display: "flex",
              alignItems: "center",
            }}
          >
            <Iconify
              icon="solar:danger-circle-linear"
              color="grey"
              width={24}
              sx={{
                marginRight: 0.5,
                transform: "rotate(180deg)",
              }}
            />
            create a new dataset to copy this span to
          </Typography>
          <Box>
            <FormControl fullWidth sx={{ mb: 2 }}>
              <input
                id="new-dataset-name"
                type="text"
                placeholder="Enter dataset name"
                style={{
                  width: "100%",
                  padding: "8px 0",
                  border: "none",
                  borderBottom: "2px solid var(--border-default)",
                  outline: "none",
                  fontSize: "16px",
                }}
              />
            </FormControl>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={onClose}
            color="primary"
            sx={{
              border: "1px solid grey",
            }}
          >
            Cancel
          </Button>
          <Button onClick={onClose} color="primary" variant="contained">
            Next
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

CreateNewDataset.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default CreateNewDataset;
