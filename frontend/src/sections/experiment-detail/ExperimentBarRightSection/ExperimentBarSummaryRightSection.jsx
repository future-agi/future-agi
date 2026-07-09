import { Button, Box, CircularProgress } from "@mui/material";
import React, { useState } from "react";
import { useExperimentDetailContext } from "../experiment-context";
import { trackEvent, Events } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";

const ExperimentBarSummaryRightSection = () => {
  const { setChooseWinnerOpen } = useExperimentDetailContext();
  const { experimentId } = useParams();
  const [isDownloading, setIsDownloading] = useState(false);

  const { mutate: downloadExperiment } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.develop.experiment.downloadExperiment(experimentId), {
        responseType: "blob",
      }),
    onMutate: () => {
      setIsDownloading(true);
    },
    onSuccess: (response) => {
      setIsDownloading(false);
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `experiment-${experimentId}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Experiment data downloaded successfully", {
        variant: "success",
      });
    },
    onError: () => {
      setIsDownloading(false);
      enqueueSnackbar("Failed to download experiment data", {
        variant: "error",
      });
    },
  });

  return (
    <Box sx={{ display: "flex", gap: 1 }}>
      <Button
        variant="outlined"
        startIcon={
          isDownloading ? (
            <CircularProgress size={16} />
          ) : (
            <Iconify icon="material-symbols:download" />
          )
        }
        onClick={() => {
          trackEvent(Events.experimentDownloadClicked);
          downloadExperiment();
        }}
        disabled={isDownloading}
      >
        Download CSV
      </Button>
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
