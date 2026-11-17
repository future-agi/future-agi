import { LoadingButton } from "@mui/lab";
import {
  Box,
  Dialog,
  DialogActions,
  DialogTitle,
  Divider,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import { useAgentDetailsStore } from "../store/agentDetailsStore";

const SaveNewAgentVersionModal = ({
  open,
  onClose,
  control,
  handleSubmit,
  onSubmit,
  isSubmitting,
}) => {
  const { latestVersionNumber } = useAgentDetailsStore();
  const handleSave = async () => {
    await handleSubmit(onSubmit)();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          width: 560,
          borderRadius: 3,
        },
      }}
    >
      {/* Title Section */}
      <DialogTitle sx={{ px: 2.5, pb: 2, pt: 1.5 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography
            fontWeight="fontWeightMedium"
            color="text.primary"
            typography="m3"
          >
            Save version {latestVersionNumber + 1}
          </Typography>

          <IconButton onClick={onClose} size="small">
            <Iconify
              icon="solar:close-linear"
              color="text.primary"
              width={26}
              height={26}
            />
          </IconButton>
        </Box>

        <Typography
          typography={"s2_1"}
          fontWeight={"fontWeightRegular"}
          sx={{ color: "text.disabled" }}
        >
          Save the version with a commit message to clearly document the changes
          made.
        </Typography>
      </DialogTitle>

      <Divider sx={{ borderColor: "divider" }} />
      <Box sx={{ px: 2.5, pt: 2 }}>
        <FormTextFieldV2
          control={control}
          fieldName="commitMessage"
          label="Commit Message"
          placeholder="Describe your changes"
          size="small"
          fullWidth
          required
          sx={{
            "& .MuiInputLabel-root": {
              fontWeight: 500,
            },
          }}
        />
      </Box>

      {/* Footer Buttons */}
      <DialogActions sx={{ px: 2.5, flexDirection: "column", pb: 2 }}>
        <LoadingButton
          variant="contained"
          fullWidth
          color="primary"
          size="medium"
          loading={isSubmitting}
          onClick={handleSave}
        >
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            sx={{ color: "common.white" }}
          >
            Save
          </Typography>
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

SaveNewAgentVersionModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  control: PropTypes.object,
  handleSubmit: PropTypes.func,
  onSubmit: PropTypes.func,
  isSubmitting: PropTypes.bool,
};

export default SaveNewAgentVersionModal;
