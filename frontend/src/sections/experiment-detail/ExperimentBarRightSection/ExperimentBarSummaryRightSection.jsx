import { Box, Button, CircularProgress } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import React, { useRef, useState } from "react";
import { useParams } from "react-router";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { useExperimentDetailContext } from "../experiment-context";
import { trackEvent, Events } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";

const ExperimentBarSummaryRightSection = () => {
  const { experimentId } = useParams();
  const canDownloadExperiment = Boolean(experimentId);
  const downloadInFlightRef = useRef(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const { setChooseWinnerOpen } = useExperimentDetailContext();

  const { mutate: downloadExperiment } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.develop.experiment.downloadExperiment(experimentId), {
        responseType: "blob",
      }),
    onMutate: () => {
      setIsDownloading(true);
    },
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `experiment-${experimentId}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Experiment downloaded successfully", {
        variant: "success",
      });
    },
    onError: () => {
      enqueueSnackbar("Failed to download experiment", {
        variant: "error",
      });
    },
    onSettled: () => {
      downloadInFlightRef.current = false;
      setIsDownloading(false);
    },
    meta: { errorHandled: true },
  });

  const handleDownload = () => {
    if (!experimentId || downloadInFlightRef.current) return;

    downloadInFlightRef.current = true;
    trackEvent(Events.expDownloadClicked);
    downloadExperiment();
  };

  return (
    <Box display="flex" alignItems="center" gap={1.5}>
      {canDownloadExperiment && (
        <Button
          variant="outlined"
          startIcon={
            isDownloading ? (
              <CircularProgress color="inherit" size={16} />
            ) : (
              <Iconify icon="material-symbols:download" />
            )
          }
          disabled={isDownloading}
          onClick={handleDownload}
        >
          {isDownloading ? "Downloading…" : "Download CSV"}
        </Button>
      )}
      <Button
        variant="contained"
        color="primary"
        startIcon={<Iconify icon="mdi:crown-outline" />}
        onClick={() => {
          trackEvent(Events.expWinnerClick);
          setChooseWinnerOpen(true);
        }}
      >
        Choose winner
      </Button>
    </Box>
  );
};

export default ExperimentBarSummaryRightSection;
