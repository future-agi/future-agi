import {
  Box,
  Button,
  Drawer,
  IconButton,
  InputAdornment,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify/iconify";
import { DatasetSelector } from "./DatasetSelector";
import "./CreateUpdateMetric.css";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useForm } from "react-hook-form";
import { useParams } from "react-router";
import { zodResolver } from "@hookform/resolvers/zod";
import { CreateMetricFormValidation } from "./validation";
import LoadingButton from "@mui/lab/LoadingButton";
import { useSnackbar } from "src/components/snackbar";
import { useGetMetricOptions } from "../../../../../api/model/metric";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { FormSelectField } from "src/components/FormSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

export const CreateUpdateCustomMetric = ({ show, onClose, updateData }) => {
  return (
    <>
      <Drawer
        anchor="right"
        open={show}
        onClose={onClose}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "550px",
            position: "fixed",
            zIndex: 9999,
            borderRadius: "10px",
            backgroundColor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <CustomMetricForm onClose={onClose} updateData={updateData} />
      </Drawer>
    </>
  );
};

const evaluationTypeOptions = [
  { label: "Output", value: "EVALUATE_CHAT" },
  { label: "RAG Context", value: "EVALUATE_CONTEXT" },
  { label: "RAG Ranking", value: "EVAL_CONTEXT_RANKING" },
];

const CustomMetricForm = React.forwardRef(({ onClose, updateData }, ref) => {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const promptSnapshot = useRef("");

  const mode = updateData ? "update" : "create";

  // const [isTested, setIsTested] = useState(false);

  const [suggestedPrompt, setSuggestedPrompt] = useState(null);

  const { data: datasetOptions } = useGetMetricOptions(id);

  const { mutate: createMetric, isPending: isLoadingCreate } = useMutation({
    mutationFn: (body) => axios.post(`${endpoints.customMetric.create}`, body),
    onSuccess: () => {
      enqueueSnackbar("Custom Metric created successfully", {
        variant: "success",
      });
      onClose();
      queryClient.invalidateQueries({ queryKey: ["customMetric"] });
      queryClient.invalidateQueries({ queryKey: ["model", id] });
      queryClient.invalidateQueries({ queryKey: ["all-custom-metric", id] });
    },
    onError: (data) => {
      if (typeof data?.result === "string") {
        enqueueSnackbar({
          message: data?.result,
          variant: "error",
          autoHideDuration: 5000,
        });
      }
    },
  });

  const { mutate: editMetric, isPending: isLoadingEdit } = useMutation({
    mutationFn: (body) => axios.post(`${endpoints.customMetric.edit}`, body),
    onSuccess: () => {
      enqueueSnackbar("Custom Metric Edited successfully", {
        variant: "success",
      });
      onClose();
      queryClient.invalidateQueries({ queryKey: ["customMetric"] });
      queryClient.invalidateQueries({
        queryKey: ["dataset-options", id, updateData.id],
      });
      queryClient.invalidateQueries({
        queryKey: ["metric-tag-options", id],
      });
    },
  });

  const { handleSubmit, control, watch, setValue, setError } = useForm({
    resolver: zodResolver(CreateMetricFormValidation),
    defaultValues: updateData
      ? updateData
      : {
          name: "",
          datasets: [],
          metricType: 2,
          evaluationType: null,
        },
  });

  const { mutate: testMetric, isPending: isTesting } = useMutation({
    mutationFn: (d) => axios.post(endpoints.customMetric.testMetric, d),
    onSuccess: (data, v) => {
      setSuggestedPrompt(data?.data?.prompts);
      promptSnapshot.current = v?.prompt;
    },
    onError: (d) => {
      promptSnapshot.current = null;
      setError("prompt", { type: "custom", message: d.message });
    },
  });

  const prompt = watch("prompt");

  const onSubmit = (formValues) => {
    if (mode === "create") {
      createMetric({ ...formValues, modelId: id });
      // trackEvent(Events.customMetricCreateComplete, trackObject(formValues));
    } else {
      editMetric({ ...formValues, id: updateData.id });
      // trackEvent(Events.customMetricEditComplete, trackObject(formValues));
    }
  };

  const testButtonDisabled =
    !prompt?.length ||
    (promptSnapshot.current && promptSnapshot.current === prompt);

  const isTested = promptSnapshot.current && promptSnapshot.current === prompt;

  return (
    <>
      <form
        style={{ display: "flex", height: "100%" }}
        ref={ref}
        onSubmit={handleSubmit(onSubmit)}
      >
        <Box
          sx={{
            padding: 2,
            flex: 1,
            display: "flex",
            flexDirection: "column",
            zIndex: 21,
          }}
        >
          <Box>
            <Typography variant="subtitle1" color="text.disabled">
              {mode === "create" ? "Create" : "Edit"} Custom Metric
            </Typography>
            <IconButton
              onClick={() => onClose()}
              sx={{ position: "absolute", top: "12px", right: "12px" }}
            >
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Box>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              pt: 2,
              flex: 1,
            }}
          >
            <FormTextFieldV2
              label="Name"
              sx={{
                "& .MuiInputBase-input": {
                  // fontSize: "24px",
                  fontWeight: 700,
                },
                "& .MuiInputLabel-root": {
                  // fontSize: "24px", // Default font size
                },
              }}
              autoFocus
              placeholder="Untitled"
              control={control}
              fieldName="name"
            />
            <FormSelectField
              control={control}
              fieldName="evaluationType"
              fullWidth
              options={evaluationTypeOptions}
              size="small"
              label="Evaluation"
            />
            <DatasetSelector
              datasetOptions={datasetOptions}
              control={control}
            />
            {/* <EvaluationSelector control={control} /> */}
            <Tooltip
              open={Boolean(suggestedPrompt)}
              placement="left-start"
              slotProps={{
                tooltip: {
                  style: {
                    backgroundColor: "var(--bg-paper)",
                    maxWidth: "500px",
                  },
                },
              }}
              title={
                <>
                  <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                    <IconButton
                      onClick={() => setSuggestedPrompt(null)}
                      size="small"
                    >
                      <Iconify icon="mingcute:close-line" width={16} />
                    </IconButton>
                  </Box>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 1.5,
                      alignItems: "flex-end",
                      paddingY: 1,
                    }}
                  >
                    <TextField
                      label={
                        <Box sx={{ display: "flex", alignItems: "center" }}>
                          <Iconify icon="tabler:bulb" /> Suggested Prompt
                        </Box>
                      }
                      sx={{ width: "300px" }}
                      multiline
                      variant="filled"
                      InputProps={{ readOnly: true }}
                      value={suggestedPrompt}
                    />
                    <Box>
                      <Button
                        onClick={() => {
                          setValue("prompt", suggestedPrompt);
                          setSuggestedPrompt(false);
                          promptSnapshot.current = suggestedPrompt;
                        }}
                        variant="contained"
                        color="primary"
                        size="small"
                      >
                        Use This
                      </Button>
                    </Box>
                  </Box>
                </>
              }
              disableFocusListener
              disableHoverListener
              disableTouchListener
            >
              <Box sx={{ flex: 1 }}>
                <FormTextFieldV2
                  control={control}
                  fieldName="prompt"
                  label="Define your metric"
                  placeholder="Define your metric"
                  fullWidth
                  variant="filled"
                  multiline
                  rows={10}
                  InputProps={{
                    endAdornment: (
                      <InputAdornment position="end">
                        <Box
                          sx={{
                            backgroundColor: "background.paper",
                            color: "text.secondary",
                            fontSize: "0.75rem",
                            width: "100%",
                            marginBottom: "100px",
                            whiteSpace: "normal",
                            padding: "12px",
                            borderRadius: "0 0px 8px 8px",
                          }}
                        >
                          <Typography
                            color="text.disabled"
                            fontSize={12}
                            fontWeight={600}
                          >
                            Define your metric in Natural Language
                          </Typography>
                          <Typography color="text.disabled" fontSize={12}>
                            Eg:
                            <br />
                            1. Create metric to measure hallucination in the
                            chat
                            <br />
                            2. Check if the chatbot can understand different
                            languages and gives right answers
                          </Typography>
                        </Box>
                      </InputAdornment>
                    ),
                  }}
                  sx={{
                    "& .MuiFilledInput-input	": {
                      marginBottom: "100px",
                    },
                    "& .MuiFilledInput-root": {
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "stretch",
                      "&:after, &:before": {
                        display: "none",
                      },
                    },
                    "& .MuiInputAdornment-root": {
                      margin: 0,
                      width: "100%",
                    },
                  }}
                />
              </Box>
            </Tooltip>

            <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 2 }}>
              <LoadingButton
                loading={isTesting}
                variant="contained"
                color="primary"
                onClick={() => testMetric({ prompt })}
                disabled={testButtonDisabled}
                type="button"
              >
                Test metric
              </LoadingButton>
              <CustomTooltip
                show={!isTested}
                title={`Test metric before ${mode === "create" ? "Create" : "Edit"} metric`}
                placement="top"
                arrow
              >
                <Box>
                  <LoadingButton
                    disabled={!isTested}
                    loading={isLoadingCreate || isLoadingEdit}
                    variant="contained"
                    color="primary"
                    type="submit"
                  >
                    {mode === "create" ? "Create" : "Edit"} metric
                  </LoadingButton>
                </Box>
              </CustomTooltip>
            </Box>
          </Box>
        </Box>
      </form>
    </>
  );
});

CustomMetricForm.propTypes = {
  onClose: PropTypes.func,
  updateData: PropTypes.object,
};

CustomMetricForm.displayName = "CustomMetricForm";

CreateUpdateCustomMetric.propTypes = {
  show: PropTypes.bool,
  onClose: PropTypes.func,
  updateData: PropTypes.object,
};
