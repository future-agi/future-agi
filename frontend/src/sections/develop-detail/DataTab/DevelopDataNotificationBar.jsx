import React, { useState } from "react";
import { Alert, AlertTitle, Button, IconButton, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import DevelopDataNotificationModal from "./DevelopDataNotificationModal";
import { ShowComponent } from "src/components/show";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";

const UploadProgressNotification = ({ status, showReason, onClose }) => {
  const [showWarningModal, setShowWarningModal] = useState(false);

  const { dataset } = useParams();

  const { mutate: updateDataset } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
  });

  const onWarningClose = () => {
    setShowWarningModal(false);
  };

  const onBannerDismiss = () => {
    //@ts-ignore
    updateDataset({
      dataset_config: {
        dismiss_banner: true,
      },
    });

    onClose();
  };

  return (
    <>
      <ShowComponent
        condition={status?.datasetStatus == "PartialUploadProgress"}
      >
        <Alert severity="info" onClose={onBannerDismiss} sx={{ mb: 1.5 }}>
          <AlertTitle>Partial Upload in Progress</AlertTitle>
          {
            "We've fetched some data from your CSV, and the extraction process is still running."
          }
        </Alert>
      </ShowComponent>
      <ShowComponent
        condition={status?.datasetStatus == "PartialDataExtracted"}
      >
        <Alert
          severity="warning"
          action={
            <Stack direction="row" alignItems="center" gap={0.5}>
              {showReason && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setShowWarningModal(true)}
                >
                  Check reasons
                </Button>
              )}
              <IconButton
                size="small"
                color="inherit"
                aria-label="Close"
                onClick={onBannerDismiss}
              >
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton>
            </Stack>
          }
          sx={{ mb: 1.5 }}
        >
          <AlertTitle>Partial Data Extracted</AlertTitle>
          {
            "We’ve extracted some data from your CSV, but a few items couldn’t be processed."
          }
        </Alert>
      </ShowComponent>
      <DevelopDataNotificationModal
        open={showWarningModal}
        onClose={onWarningClose}
        onClick={onWarningClose}
        data={status}
      />
    </>
  );
};

UploadProgressNotification.propTypes = {
  showReason: PropTypes.bool,
  onClose: PropTypes.func,
  status: PropTypes.string,
};

export default UploadProgressNotification;
