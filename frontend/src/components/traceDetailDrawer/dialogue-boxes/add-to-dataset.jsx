import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Typography,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import CreateNewDataset from "./create-new-dataset";

const AddToDataset = ({ open, onClose, onOpenCreateNewDataset }) => {
  return (
    <Box>
      <Dialog
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            width: "50%",
            maxWidth: "80%",
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
        <DialogTitle variant="h4" sx={{ marginLeft: 2, paddingBottom: 1 }}>
          Add to Dataset
        </DialogTitle>

        <Typography
          variant="subtitle2"
          sx={{
            marginBottom: 2,
            marginLeft: 4,
            color: "grey",
            display: "flex",
            alignItems: "center",
          }}
        >
          <Iconify
            icon="solar:danger-circle-linear"
            color="grey" // Icon color
            width={24}
            sx={{
              marginRight: 0.5,
              transform: "rotate(180deg)",
            }}
          />
          Copy this span to a dataset or create a new dataset to copy this span
          to
        </Typography>

        <DialogContent>
          <Box>
            <FormControl sx={{ m: 1, minWidth: 80, marginBottom: 3 }} fullWidth>
              <InputLabel id="demo-simple-select-autowidth-label">
                Dataset
              </InputLabel>
              <Select
                labelId="demo-simple-select-autowidth-label"
                id="demo-simple-select-autowidth"
                label="Dataset"
              >
                <MenuItem value=""></MenuItem>
                <MenuItem value="dataset1">Dataset 1</MenuItem>
                <MenuItem value="dataset2">Dataset 2</MenuItem>
                <MenuItem value="dataset3">Dataset 3</MenuItem>
              </Select>
            </FormControl>
          </Box>

          <Button
            sx={{
              display: "flex",
              alignItems: "center",
              color: "primary.main",
              paddingLeft: 1,
            }}
            onClick={onOpenCreateNewDataset} // Trigger transition
          >
            <Iconify icon="solar:add-linear" color="primary.main" width={24} />
            Create New Dataset
          </Button>
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
            Add to Dataset
          </Button>
        </DialogActions>
      </Dialog>
      <CreateNewDataset />
    </Box>
  );
};

AddToDataset.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onOpenCreateNewDataset: PropTypes.func.isRequired, // New prop
};

export default AddToDataset;
