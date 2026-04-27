import { Box, Button, Card, Typography } from "@mui/material";
import React, { useState } from "react";
import ModelBaselineCard from "./ModelBaselineCard";
import ModelPerformanceConfigCard from "./ModelPerformanceConfigCard";
import DeleteConfirm from "./DeleteConfirm";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "src/routes/hooks";
import { useNavigate } from "react-router";
import { useGetModelDetail } from "src/api/model/model";

const ModeConfig = () => {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const { id } = useParams();

  const { data: modelDetails } = useGetModelDetail(id);

  //@ts-ignore
  const { defaultMetric, isMetricAdded } = modelDetails;

  const { mutate: onModelDelete, isPending: isDeleting } = useMutation({
    mutationFn: () => axios.delete(endpoints.model.deleteModel(id)),
    onSuccess: () => {
      setDeleteOpen(false);
      navigate("/dashboard/models");
      // trackEvent(Events.configDeleteModelComplete);
    },
  });

  const navigate = useNavigate();

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 3.75,
        paddingX: "20px",
        paddingTop: "20px",
        // paddingX: 3.75,
        // paddingBottom: 3.75,
      }}
    >
      <ModelBaselineCard modelDetails={modelDetails} />
      <ModelPerformanceConfigCard
        isMetricAdded={isMetricAdded}
        defaultMetricId={defaultMetric}
      />
      <Card>
        <Box
          sx={{
            padding: 2.5,
            display: "flex",
            justifyContent: "space-between",
            borderBottom: 1,
            borderColor: "divider",
            alignItems: "center",
          }}
        >
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            Model Settings
          </Typography>
        </Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            p: 2.5,
          }}
        >
          <Typography variant="body2">Delete modal</Typography>
          <Button
            variant="contained"
            color="error"
            onClick={() => {
              setDeleteOpen(true);
              // trackEvent(Events.configDeleteModelStart);
            }}
          >
            Delete
          </Button>
        </Box>
      </Card>
      <DeleteConfirm
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleteClick={() => onModelDelete()}
        loading={isDeleting}
      />
    </Box>
  );
};

ModeConfig.propTypes = {};

export default ModeConfig;
