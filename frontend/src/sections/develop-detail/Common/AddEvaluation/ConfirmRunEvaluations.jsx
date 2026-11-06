import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
} from "@mui/material";
import Iconify from "src/components/iconify";
import EvalColumnChip from "../../Evaluation/EvalColumnChip";

const ConfirmRunEvaluations = ({
  open,
  onClose,
  onConfirm,
  selectedUserEvalList,
}) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography variant="h6">
            Are you sure you want to run the following evaluations?
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
        <Typography variant="body1" color="text.secondary">
          This will overwrite the previous evaluation results.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        {selectedUserEvalList?.map((e) => (
          <EvalColumnChip key={e.id} text={e.name} />
        ))}
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          autoFocus
          color="primary"
        >
          Run Evaluations
        </Button>
      </DialogActions>
    </Dialog>
  );
};

ConfirmRunEvaluations.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  selectedUserEvalList: PropTypes.array,
};

export default ConfirmRunEvaluations;
