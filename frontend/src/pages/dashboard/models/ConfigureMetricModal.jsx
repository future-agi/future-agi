import { Box, Button, IconButton, Typography } from "@mui/material";
import React from "react";
import { ModalComponent } from "src/components/ModalComponent";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useNavigate, useParams } from "react-router";

const ConfigureMetricModal = ({ open, onClose }) => {
  const navigate = useNavigate();
  const { id } = useParams();

  return (
    <ModalComponent open={open} onClose={onClose}>
      <Box
        sx={{
          padding: 3,
          display: "flex",
          gap: "12px",
          flexDirection: "column",
          position: "relative",
        }}
      >
        <IconButton
          onClick={() => onClose()}
          sx={{ position: "absolute", top: "12px", right: "12px" }}
        >
          <Iconify icon="mingcute:close-line" />
        </IconButton>
        <Typography variant="h5">Define Metric</Typography>
        <Typography
          variant="body1"
          color="text.disabled"
        >{`You haven't defined a metric`}</Typography>
        <Box sx={{ display: "flex", justifyContent: "end" }}>
          <Button
            startIcon={<Iconify icon="eva:diagonal-arrow-right-up-fill" />}
            color="primary"
            onClick={() => navigate(`/dashboard/models/${id}/custom-metrics`)}
          >
            Define Metric
          </Button>
        </Box>
      </Box>
    </ModalComponent>
  );
};

ConfigureMetricModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default ConfigureMetricModal;
