import {
  Box,
  Button,
  CircularProgress,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import React from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { useSimulationDetailContext } from "./context/SimulationDetailContext";

const EmptyExecutionsState = () => {
  const theme = useTheme();
  const { id: promptTemplateId } = useParams();
  const { simulation, refetchExecutions } = useSimulationDetailContext();

  const { mutate: executeSimulation, isPending: isExecuting } = useMutation({
    mutationFn: async () => {
      return axios.post(
        endpoints.promptSimulation.execute(promptTemplateId, simulation?.id),
        {},
      );
    },
    onSuccess: () => {
      enqueueSnackbar("Simulation started successfully", {
        variant: "success",
      });
      refetchExecutions();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.error || "Failed to start simulation",
        { variant: "error" },
      );
    },
  });

  return (
    <Box
      display="flex"
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      height="100%"
      minHeight={300}
      gap={2}
      sx={{
        backgroundColor: theme.palette.background.neutral,
        borderRadius: 2,
        padding: 4,
      }}
    >
      <Box
        sx={{
          width: 64,
          height: 64,
          borderRadius: "50%",
          backgroundColor: theme.palette.info.lighter,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Iconify
          icon="mdi:play-circle-outline"
          width={32}
          height={32}
          color={theme.palette.info.main}
        />
      </Box>

      <Typography variant="h6" color="text.primary" textAlign="center">
        No Executions Yet
      </Typography>

      <Typography
        variant="body2"
        color="text.secondary"
        textAlign="center"
        maxWidth={400}
      >
        Run this simulation to start generating conversation executions. Each
        execution will run through your selected scenarios and evaluate the
        results.
      </Typography>

      <Button
        variant="contained"
        startIcon={
          isExecuting ? (
            <CircularProgress size={16} color="inherit" />
          ) : (
            <Iconify icon="mdi:play" />
          )
        }
        onClick={() => executeSimulation()}
        disabled={isExecuting}
        sx={{ mt: 2 }}
      >
        {isExecuting ? "Starting..." : "Run Simulation Now"}
      </Button>
    </Box>
  );
};

export default EmptyExecutionsState;
