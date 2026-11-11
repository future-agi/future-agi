import {
  Box,
  Button,
  Drawer,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { useForm } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { LoadingButton } from "@mui/lab";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

import WeightSliderSection from "./WeightSliderSection";
import WeightSlider from "./WeightSlider";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const getDefaultValues = (columns) => {
  const values = {};
  columns.forEach((column) => {
    values[column.id] = column.value ?? 5;
  });
  // Backend expects snake_case keys in `config`
  // (see tracer/views/project_version.py).
  values.avg_cost = values.avg_cost ?? 5;
  values.avg_latency_ms = values.avg_latency_ms ?? 5;

  return values;
};

const ChooseProjectExperimentWinnerDrawerChild = ({
  onClose,
  columns,
  refreshGrid,
}) => {
  const { control, handleSubmit } = useForm({
    defaultValues: getDefaultValues(columns),
  });
  const evalMetrics = columns.filter((c) => c.groupBy === "Evaluation Metrics");
  const { projectId } = useParams();
  const theme = useTheme();

  const { mutate: chooseWinner, isPending } = useMutation({
    mutationFn: (d) =>
      axios.post(endpoints.project.chooseWinner(), {
        project_id: projectId,
        config: d,
      }),
    onSuccess: (data, variables) => {
      onClose();
      refreshGrid();
      trackEvent(Events.pExperimentWinnerSettingsApplied, {
        [PropertyName.formFields]: {
          projectId,
          config: variables,
        },
      });
    },
  });

  return (
    <Box
      sx={{
        // padding: "20px",
        width: "550px",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: theme.spacing(2, 2.5, 0),
        }}
      >
        <Typography
          variant="m3"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          Winner Settings
        </Typography>
        <IconButton size="small" onClick={onClose}>
          <Iconify
            icon="solar:close-linear"
            color="text.primary"
            width="12"
            height="12"
          />
        </IconButton>
      </Box>
      {/* <Typography variant="caption" sx={{ marginTop: 0 }}>
        On a scale of{" "}
        <Typography variant="caption" fontWeight={600} component="span">
          0(not important)
        </Typography>{" "}
        to{" "}
        <Typography variant="caption" fontWeight={600} component="span">
          10(very important)
        </Typography>{" "}
        adjust the slider for each metric below
      </Typography> */}
      <Typography
        sx={{
          fontWeight: 400,
          fontSize: "13px",
          color: "text.disabled",
          padding: theme.spacing(0, 2, 0),
        }}
      >
        Choose the overall importance value of the given variable below to
        determine which dataset performed the best
      </Typography>
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: "70px",
          overflow: "auto",
          mt: 0.5,
          padding: theme.spacing(0, 2.5, 2),
        }}
      >
        {evalMetrics?.length > 0 && (
          <WeightSliderSection
            title="Evaluation Metrics"
            listSx={{
              gap: evalMetrics?.length > 1 ? "66px" : "20px",
            }}
          >
            {evalMetrics?.map((column) => (
              <WeightSlider
                key={column.id}
                control={control}
                label={column.name}
                fieldName={column.id}
              />
            ))}
          </WeightSliderSection>
        )}
        <WeightSliderSection
          title="System Metrics"
          listSx={{
            gap: "66px",
          }}
        >
          <WeightSlider
            control={control}
            label="Avg Cost"
            fieldName="avg_cost"
          />
          <WeightSlider
            control={control}
            label="Avg Latency"
            fieldName="avg_latency_ms"
          />
        </WeightSliderSection>

        {/* <WeightSlider control={control} /> */}
      </Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          padding: theme.spacing(0, 2.5, 2),
        }}
      >
        <Button
          variant="outlined"
          size="small"
          fullWidth
          onClick={onClose}
          sx={{ color: "text.disabled", fontWight: 500 }}
        >
          Cancel
        </Button>
        <LoadingButton
          color="primary"
          size="small"
          variant="contained"
          fullWidth
          loading={isPending}
          onClick={handleSubmit(chooseWinner)}
        >
          Choose Winner
        </LoadingButton>
      </Box>
    </Box>
  );
};

ChooseProjectExperimentWinnerDrawerChild.propTypes = {
  onClose: PropTypes.func,
  columns: PropTypes.array,
  refreshGrid: PropTypes.func,
};

const ChooseProjectExperimentWinnerDrawer = ({
  open,
  onClose,
  columns,
  refreshGrid,
}) => {
  const covered = useDrawerCovered(open);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      // onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <ChooseProjectExperimentWinnerDrawerChild
        onClose={onClose}
        columns={columns}
        refreshGrid={refreshGrid}
      />
    </Drawer>
  );
};

ChooseProjectExperimentWinnerDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  columns: PropTypes.array,
  refreshGrid: PropTypes.func,
};

export default ChooseProjectExperimentWinnerDrawer;
