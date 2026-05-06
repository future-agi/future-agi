import {
  Box,
  Card,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Typography,
} from "@mui/material";
import React, { useState } from "react";
import { useGetAllCustomMetrics } from "src/api/model/metric";
import PropType from "prop-types";
import { useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";
import LoadingButton from "@mui/lab/LoadingButton";
import CustomTooltip from "src/components/tooltip";

const ModelPerformanceConfigCard = ({ defaultMetricId, isMetricAdded }) => {
  const { id } = useParams();

  const { data: allCustomMetrics } = useGetAllCustomMetrics(id);

  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();

  const { mutate: updateMetricId, isPending } = useMutation({
    mutationFn: (body) =>
      axios.post(`${endpoints.model.updateMetric}${id}/`, body),
    onSuccess: () => {
      enqueueSnackbar("Default metric updated", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["model", id] });
      // trackEvent(Events.configConfigureDefaultMetric, trackObject(v));
    },
  });

  const [selectedMetricId, setSelectedMetricId] = useState(defaultMetricId);
  return (
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
          Performance Configs
        </Typography>
        <LoadingButton
          color="primary"
          variant="contained"
          disabled={selectedMetricId === defaultMetricId}
          onClick={() => updateMetricId({ metricId: selectedMetricId })}
          loading={isPending}
        >
          Save
        </LoadingButton>
      </Box>
      <Box sx={{ p: 2.5, display: "flex", gap: 3, flexDirection: "column" }}>
        <Box sx={{ display: "flex", gap: 2, width: "100%" }}>
          <CustomTooltip
            show={!isMetricAdded}
            placement="top"
            title="Custom Metric is not created"
            arrow
            followCursor
          >
            <FormControl fullWidth size="small">
              <InputLabel>Default Metric</InputLabel>
              <Select
                disabled={!allCustomMetrics?.length}
                value={selectedMetricId}
                onChange={(e) => setSelectedMetricId(e.target.value)}
                label="Default Metric"
                sx={{}}
                MenuProps={{
                  PaperProps: {
                    sx: {
                      maxHeight: 48 * 4.5 + 8, // Example: change this to your desired max height
                    },
                  },
                }}
              >
                {allCustomMetrics?.map(({ id, name }) => (
                  <MenuItem key={id} value={id}>
                    {name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </CustomTooltip>
        </Box>
      </Box>
    </Card>
  );
};

ModelPerformanceConfigCard.propTypes = {
  defaultMetricId: PropType.string,
  isMetricAdded: PropType.bool,
};

export default ModelPerformanceConfigCard;
