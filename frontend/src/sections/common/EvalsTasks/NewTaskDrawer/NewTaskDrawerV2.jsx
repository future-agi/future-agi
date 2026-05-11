import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  useForm,
  useWatch,
  useFieldArray,
  useFormState,
} from "react-hook-form";
import {
  Box,
  Typography,
  Button,
  Chip,
  Drawer,
  IconButton,
  TextField,
  FormHelperText,
  Link,
  useTheme,
} from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import NewTaskFilterBox from "./NewTaskFilterBox";
import ScheduledRuns from "./ScheduledRuns";
import _ from "lodash";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQuery } from "@tanstack/react-query";
import { formatDate } from "src/utils/report-utils";
import { endOfToday, sub } from "date-fns";
import { zodResolver } from "@hookform/resolvers/zod";
import { getNewTaskFilters, NewTaskValidationSchema } from "./validation";
import { enqueueSnackbar } from "src/components/snackbar";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { useNavigate } from "react-router";
import FilterErrorBoundary from "src/components/ComplexFilter/FilterErrorBoundary";
import { objectCamelToSnake } from "src/utils/utils";
import { EvalPickerDrawer } from "../../EvalPicker";

// ── Configured Eval Card ──

const ConfiguredEvalCard = ({ evalItem, onRemove }) => {
  const theme = useTheme();
  const name =
    evalItem?.name ||
    evalItem?.eval_template?.name ||
    evalItem?.eval_template_name ||
    "Evaluation";
  const mappedKeys = evalItem?.mapping ? Object.keys(evalItem.mapping) : [];

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor:
          theme.palette.mode === "dark"
            ? "rgba(255,255,255,0.02)"
            : "rgba(0,0,0,0.01)",
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" fontWeight={500} noWrap>
          {name}
        </Typography>
        {mappedKeys.length > 0 && (
          <Box sx={{ display: "flex", gap: 0.5, mt: 0.5, flexWrap: "wrap" }}>
            {mappedKeys.slice(0, 3).map((key) => (
              <Chip
                key={key}
                label={`${key} → ${evalItem.mapping[key]}`}
                size="small"
                sx={{
                  fontSize: "10px",
                  height: 18,
                  bgcolor: "action.hover",
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
            {mappedKeys.length > 3 && (
              <Chip
                label={`+${mappedKeys.length - 3}`}
                size="small"
                sx={{
                  fontSize: "10px",
                  height: 18,
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            )}
          </Box>
        )}
      </Box>
      <IconButton
        size="small"
        onClick={onRemove}
        sx={{ p: 0.25, color: "text.secondary" }}
      >
        <Iconify icon="mingcute:close-line" width={16} />
      </IconButton>
    </Box>
  );
};

ConfiguredEvalCard.propTypes = {
  evalItem: PropTypes.object.isRequired,
  onRemove: PropTypes.func.isRequired,
};

// ── Main Component ──

const NewTaskDrawerV2 = ({
  open,
  onClose,
  projectDetails,
  refreshGrid,
  observeId = null,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [evalPickerOpen, setEvalPickerOpen] = useState(false);

  const { control, reset, handleSubmit, getValues, setValue } = useForm({
    defaultValues: {
      name: "",
      project: observeId ? observeId : "",
      filters: [],
      spansLimit: "",
      samplingRate: 100,
      evalsDetails: [],
      rowType: "spans",
      startDate: formatDate(sub(new Date(), { months: 6 })),
      endDate: formatDate(endOfToday()),
      runType: "historical",
    },
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  const handleClose = useCallback(() => {
    onClose();
    reset();
  }, [onClose, reset]);

  const project = useWatch({ control, name: "project" });
  const rowType = useWatch({ control, name: "rowType" }) || "spans";
  const isProjectSelected = !!project;

  const {
    fields: configuredEvals,
    append: addEval,
    remove: removeEval,
    replace,
  } = useFieldArray({
    name: "evalsDetails",
    control,
  });
  const { errors } = useFormState({ control });

  const evalsDetailsErrorMessage = _.get(errors, "evalsDetails")?.message || "";
  const formValues = useWatch({ control });

  const filtersWithoutDate = useMemo(() => {
    return getNewTaskFilters(formValues, project, true).filters || [];
  }, [formValues, project]);

  // Fetch pre-configured evals for the project
  const { data: configuredEvalList } = useQuery({
    queryKey: ["configured-evals", project],
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskConfig(), {
        params: {
          project_id: project,
          filters: { project_id: project },
        },
      }),
    select: (d) => d.data?.result,
    enabled: !!project,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    reset({
      name: "",
      project: observeId ? observeId : "",
      filters: [],
      spansLimit: "",
      samplingRate: 100,
      evalsDetails: [],
      rowType: "spans",
      startDate: formatDate(sub(new Date(), { months: 6 })),
      endDate: formatDate(endOfToday()),
      runType: "historical",
    });
  }, [observeId, reset]);

  useEffect(() => {
    if (!configuredEvalList) return;
    replace(configuredEvalList);
  }, [configuredEvalList, replace, open]);

  const { mutate: createEvalTask } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.createEvalTask(), { ...data }),
    onSuccess: () => {
      enqueueSnackbar("Eval Task Created Successfully", {
        variant: "success",
      });
      refreshGrid();
      handleClose();
    },
  });

  const onSubmit = (data) => {
    const {
      runType,
      rowType,
      spansLimit,
      samplingRate,
      evalsDetails,
      startDate,
      endDate,
      ...restData
    } = data;
    const payload = {
      ...restData,
      run_type: runType,
      row_type: rowType,
      ...(runType !== "continuous" && spansLimit
        ? { spans_limit: spansLimit }
        : {}),
      sampling_rate: samplingRate,
      evals_details: evalsDetails,
      start_date: startDate,
      end_date: endDate,
    };
    createEvalTask(payload);
  };

  // Fetch eval attributes for variable mapping
  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", project, rowType, filtersWithoutDate],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          project_id: project,
          row_type: rowType,
          filters: JSON.stringify(objectCamelToSnake(filtersWithoutDate)),
        },
      }),
    select: (data) => data.data?.result,
    enabled: isProjectSelected,
  });

  const { data: projectsList } = useQuery({
    queryKey: ["project-list"],
    queryFn: () =>
      axios.get(endpoints.project.listProjects(), {
        params: { project_type: "observe" },
      }),
    select: (data) => data.data?.result?.projects,
  });

  // Format eval attributes as source columns for EvalPickerDrawer
  const sourceColumns = useMemo(() => {
    if (!evalAttributes) return [];
    return evalAttributes.map((attr) => ({
      headerName: attr,
      field: attr,
      name: attr,
    }));
  }, [evalAttributes]);

  // Handle adding eval from the new picker
  const handleEvalAdded = useCallback(
    async (evalConfig) => {
      const tplId = evalConfig.templateId || evalConfig.template_id;
      const existingId = evalConfig.id;
      try {
        let id;
        if (existingId) {
          const { data: resp } = await axios.patch(
            endpoints.project.updateEvalTaskConfig(existingId),
            {
              eval_template: tplId,
              name: evalConfig.name,
              model: evalConfig.model || null,
              mapping: evalConfig.mapping,
              config: evalConfig.config || {},
              error_localizer: evalConfig.errorLocalizerEnabled || false,
            },
          );
          id = resp?.result?.id ?? existingId;
        } else {
          const { data: resp } = await axios.post(
            endpoints.project.createEvalTaskConfig(),
            {
              project: project,
              eval_template: tplId,
              name: evalConfig.name,
              model: evalConfig.model || null,
              mapping: evalConfig.mapping,
              config: evalConfig.config || {},
              error_localizer: evalConfig.errorLocalizerEnabled || false,
            },
          );
          id = resp?.result?.id;
        }
        addEval({
          ...evalConfig,
          id,
          template_id: tplId,
          templateId: tplId,
        });
      } catch {
        // If backend save fails, still add locally
        addEval({ ...evalConfig, template_id: tplId, templateId: tplId });
      }
    },
    [project, addEval],
  );

  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        variant="temporary"
        onClose={handleClose}
        PaperProps={{
          sx: {
            width: { xs: "100%", sm: "100%", md: "590px" },
            height: "100vh",
            position: "fixed",
            zIndex: 10,
            boxShadow: theme.customShadows?.drawer || theme.shadows[16],
            borderRadius: "0px !important",
            backgroundColor: "background.paper",
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            height: "100%",
            p: 2,
          }}
          role="presentation"
        >
          {/* Header */}
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flex: 1,
                mr: 1,
              }}
            >
              <Typography
                variant="h6"
                fontWeight={600}
                sx={{ fontSize: "16px" }}
              >
                New Task
              </Typography>
              <Link
                href="https://docs.futureagi.com/docs/observe/features/evals"
                underline="always"
                color="primary"
                target="_blank"
                rel="noopener noreferrer"
                sx={{ fontSize: "13px" }}
              >
                Learn more
              </Link>
            </Box>
            <IconButton onClick={handleClose} sx={{ p: 0.5 }}>
              <Iconify icon="mingcute:close-line" width={20} />
            </IconButton>
          </Box>

          {/* Form */}
          <Box sx={{ flex: 1, overflow: "auto" }}>
            <form
              onSubmit={handleSubmit(onSubmit)}
              style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                gap: 8,
              }}
            >
              <Box
                sx={{
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 1.5,
                  flex: 1,
                  py: 0.5,
                }}
              >
                {/* Name */}
                <FormTextFieldV2
                  control={control}
                  fieldName="name"
                  size="small"
                  label="Name"
                  variant="outlined"
                  fullWidth
                  autoFocus
                  placeholder="Enter task name"
                  required
                />

                {/* Project */}
                {observeId ? (
                  <TextField
                    size="small"
                    label="Project"
                    variant="outlined"
                    fullWidth
                    value={projectDetails?.result?.name}
                    placeholder="Choose Project"
                    disabled
                    InputLabelProps={{ shrink: true }}
                  />
                ) : (
                  <FormSearchSelectFieldControl
                    control={control}
                    fieldName="project"
                    size="small"
                    label="Project"
                    required
                    options={
                      projectsList?.map((p) => ({
                        label: p.name,
                        value: p.id,
                      })) || []
                    }
                    style={{ width: "100%" }}
                    createLabel={
                      projectsList?.length > 0 ? null : "Add New project"
                    }
                    handleCreateLabel={() => navigate("/dashboard/observe")}
                    noOptions="No projects have been added"
                  />
                )}

                {/* Filters */}
                <FilterErrorBoundary>
                  <Accordion defaultExpanded>
                    <AccordionSummary>Filters</AccordionSummary>
                    <AccordionDetails>
                      <NewTaskFilterBox
                        attributes={
                          Array.isArray(evalAttributes)
                            ? evalAttributes.map((attr) => ({
                                label: attr,
                                value: attr,
                              }))
                            : []
                        }
                        getValues={getValues}
                        setValue={setValue}
                        control={control}
                      />
                    </AccordionDetails>
                  </Accordion>
                </FilterErrorBoundary>

                {/* Scheduled Run */}
                <Accordion defaultExpanded>
                  <AccordionSummary>Scheduled Run</AccordionSummary>
                  <AccordionDetails>
                    <ScheduledRuns control={control} dayLimit="Custom" />
                  </AccordionDetails>
                </Accordion>

                {/* Evaluations — NEW: Uses EvalPickerDrawer */}
                <Accordion defaultExpanded>
                  <AccordionSummary>Evaluations</AccordionSummary>
                  <AccordionDetails>
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "column",
                        gap: 1.5,
                      }}
                    >
                      {/* Add Eval Button */}
                      <Button
                        variant="outlined"
                        size="small"
                        disabled={!isProjectSelected}
                        onClick={() => setEvalPickerOpen(true)}
                        startIcon={
                          <Iconify icon="mingcute:add-line" width={16} />
                        }
                        sx={{
                          textTransform: "none",
                          fontWeight: 500,
                          alignSelf: "flex-start",
                        }}
                      >
                        Add Evaluation
                      </Button>

                      {!isProjectSelected && (
                        <Typography
                          variant="caption"
                          color="text.disabled"
                          sx={{ fontSize: "12px" }}
                        >
                          Select a project first to add evaluations.
                        </Typography>
                      )}

                      {/* Configured Evals List */}
                      {configuredEvals.length > 0 ? (
                        <Box
                          sx={{
                            display: "flex",
                            flexDirection: "column",
                            gap: 1,
                          }}
                        >
                          {configuredEvals.map((evalItem, index) => (
                            <ConfiguredEvalCard
                              key={evalItem.id || index}
                              evalItem={evalItem}
                              onRemove={() => removeEval(index)}
                            />
                          ))}
                        </Box>
                      ) : (
                        isProjectSelected && (
                          <Box
                            sx={{
                              p: 3,
                              textAlign: "center",
                              borderRadius: 1,
                              border: "1px dashed",
                              borderColor: "divider",
                            }}
                          >
                            <Typography
                              variant="body2"
                              color="text.disabled"
                              sx={{ fontSize: "13px" }}
                            >
                              No evaluations added yet
                            </Typography>
                          </Box>
                        )
                      )}

                      <FormHelperText
                        sx={{ pl: 1, mt: 0 }}
                        error={!!evalsDetailsErrorMessage}
                      >
                        {evalsDetailsErrorMessage}
                      </FormHelperText>
                    </Box>
                  </AccordionDetails>
                </Accordion>
              </Box>

              {/* Submit */}
              <Box sx={{ mt: 1, ml: "auto", pb: 1 }}>
                <Button
                  type="submit"
                  variant="contained"
                  color="primary"
                  sx={{ width: "200px" }}
                >
                  Save Task
                </Button>
              </Box>
            </form>
          </Box>
        </Box>
      </Drawer>

      {/* Eval Picker Drawer */}
      <EvalPickerDrawer
        open={evalPickerOpen}
        onClose={() => setEvalPickerOpen(false)}
        source="task"
        sourceColumns={sourceColumns}
        onEvalAdded={handleEvalAdded}
        existingEvals={configuredEvals}
      />
    </>
  );
};

NewTaskDrawerV2.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectDetails: PropTypes.any,
  refreshGrid: PropTypes.func,
  observeId: PropTypes.string,
};

export default NewTaskDrawerV2;
