import React, { useMemo, useState } from "react";
import {
  Typography,
  Box,
  Drawer,
  IconButton,
  useTheme,
  FormControl,
  RadioGroup,
  FormControlLabel,
  Radio,
} from "@mui/material";
import Iconify from "../../iconify";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import AddExistingDataset from "./AddExistingDataset";
import AddNewDataset from "./AddNewDataset";
import { defaultSpanFields } from "../common";

const AddDataset = ({
  handleClose,
  actionToDataset,
  spanId,
  selectedTraces,
  selectedSpans,
  currentTab,
  selectAll,
  onSuccess,
}) => {
  const [selectedOptionDataset, setSelectedOptionDataset] =
    useState("existing");

  const theme = useTheme();

  const { data: availableDatasets = [] } = useQuery({
    queryKey: ["datasets"],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetList(), {
        source: "observe",
      }),
    select: (data) => data?.data?.result?.datasets,
  });

  const { data: observationFields = [] } = useQuery({
    queryKey: ["observationFields"],
    queryFn: () =>
      axios
        .get(endpoints.project.getObservationSpanField)
        .then((res) => res.data),
    select: (data) => data?.result,
  });

  // const { data: observationSpan } = useQuery({
  //   queryKey: ["observationSpan", spanId],
  //   enabled: Boolean(spanId),
  //   queryFn: () =>
  //     axios.get(endpoints.project.getObservationSpan(spanId)),
  //   select: (data) => data?.data?.result?.observation_span,
  // });

  const getobservationFields = useMemo(() => {
    if (!observationFields?.length) return [];

    const matchedFields = observationFields.filter((field) =>
      defaultSpanFields.includes(field.name),
    );

    // Inject virtual fields that are not real model fields on the backend.
    // These are computed per-span in the dataset task from EvalLogger and Score.
    const VIRTUAL_FIELDS = [
      { name: "eval_metrics", type: "json" },
      { name: "annotation_metrics", type: "json" },
    ];

    return [...matchedFields, ...VIRTUAL_FIELDS];
  }, [observationFields]);

  return (
    <Drawer
      anchor="right"
      open={actionToDataset}
      onClose={handleClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          overflowY: "hidden",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
          width: "45vw",
          p: 2,
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      {/* Close Icon */}
      <Box sx={{ position: "absolute", top: 10, right: 7 }}>
        <IconButton onClick={handleClose}>
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </Box>
      {/* Header */}
      <Typography fontWeight={600} mb={1} color={theme.palette.text.primary}>
        Add to dataset
      </Typography>

      {/* Dataset Select */}
      <Typography variant="body2" mb={1} color={theme.palette.text.secondary}>
        Move this span to a dataset or create one
      </Typography>
      {/* options */}

      <FormControl>
        <RadioGroup
          value={selectedOptionDataset}
          onChange={(e) => setSelectedOptionDataset(e.target.value)}
          sx={{
            display: "flex",
            flexDirection: "row",
            marginLeft: 1,
            marginBottom: 1,
          }}
        >
          <FormControlLabel
            value="existing"
            control={<Radio />}
            label={
              <Typography
                sx={{
                  fontWeight: 500,
                  fontSize: "14px",
                  color: theme.palette.text.secondary,
                  paddingLeft: "5px",
                }}
              >
                Add to an existing dataset
              </Typography>
            }
          />

          <FormControlLabel
            value="new"
            control={<Radio />}
            label={
              <Typography
                sx={{
                  fontWeight: 500,
                  fontSize: "14px",
                  color: theme.palette.text.secondary,
                  paddingLeft: "5px",
                }}
              >
                Add to a new dataset
              </Typography>
            }
          />
        </RadioGroup>
      </FormControl>

      {selectedOptionDataset === "existing" ? (
        <AddExistingDataset
          handleclose={handleClose}
          selectedNode={spanId}
          availableDatasets={availableDatasets}
          observationFields={getobservationFields}
          selectedTraces={selectedTraces}
          selectedSpans={selectedSpans}
          selectAll={selectAll}
          currentTab={currentTab}
          onSuccess={onSuccess}
        />
      ) : (
        <AddNewDataset
          handleclose={handleClose}
          selectedNode={spanId}
          observationFields={getobservationFields}
          selectedTraces={selectedTraces}
          selectedSpans={selectedSpans}
          selectAll={selectAll}
          currentTab={currentTab}
          onSuccess={onSuccess}
        />
      )}
    </Drawer>
  );
};

AddDataset.propTypes = {
  handleClose: PropTypes.func,
  actionToDataset: PropTypes.bool,
  spanId: PropTypes.string,
  selectedTraces: PropTypes.array,
  selectedSpans: PropTypes.array,
  currentTab: PropTypes.string,
  selectAll: PropTypes.bool,
  onSuccess: PropTypes.func,
};

export default AddDataset;
