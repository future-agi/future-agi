import React, { useEffect, useMemo } from "react";
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
  IconButton,
  TextField,
  FormHelperText,
  Link,
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
import { useEvaluationContext } from "../../EvaluationDrawer/context/EvaluationContext";
import EvaluationSection from "./EvaluationSection";
import { useNavigate } from "react-router";
import EvaluationDrawer from "../../EvaluationDrawer/EvaluationDrawer";
import FilterErrorBoundary from "src/components/ComplexFilter/FilterErrorBoundary";
import { objectCamelToSnake } from "src/utils/utils";
import { resetEvalStore } from "src/sections/evals/store/useEvalStore";

const NewTaskDrawerChild = ({
  open,
  onClose,
  projectDetails,
  refreshGrid,
  observeId = null,
}) => {
  const { setVisibleSection } = useEvaluationContext();
  const { control, reset, handleSubmit, getValues, setValue } = useForm({
    defaultValues: {
      name: "",
      project: observeId ? observeId : "",
      filters: [],
      spansLimit: "",
      samplingRate: 100,
      evalsDetails: [],
      rowType: "spans",
      startDate: formatDate(
        sub(new Date(), {
          months: 6,
        }),
      ),
      endDate: formatDate(endOfToday()),
      runType: "historical",
    },
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  const handleClose = () => {
    onClose();
    reset();
    setVisibleSection("list");
    reset();
  };
  const navigate = useNavigate();
  const project = useWatch({ control, name: "project" });
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

  const { data: configuredEvalList, isLoading } = useQuery({
    queryKey: ["configured-evals", project],
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskConfig(), {
        params: {
          project_id: project,
          filters: {
            project_id: project,
          },
        },
      }),
    select: (data) => data.data?.result,
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
      startDate: formatDate(
        sub(new Date(), {
          months: 6,
        }),
      ),
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
      axios.post(endpoints.project.createEvalTask(), {
        ...data,
      }),
    onSuccess: () => {
      enqueueSnackbar("Eval Task Created Successfully", { variant: "success" });
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

  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", project, filtersWithoutDate],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          project_id: project,
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

  const formattedEvalAttributes = useMemo(() => {
    if (!evalAttributes) return [];
    return (
      evalAttributes?.map((attr) => ({
        headerName: attr,
        field: attr,
      })) || []
    );
  }, [evalAttributes]);

  const mappedConfiguredEvals = useMemo(() => {
    if (!isProjectSelected) return [];
    return (configuredEvals || []).map((item) => ({
      ...item,
      evalRequiredKeys: item.mapping ? Object.keys(item.mapping) : [],
    }));
  }, [observeId, configuredEvals, configuredEvalList, isProjectSelected]);

  useEffect(() => {
    return () => {
      resetEvalStore();
    };
  }, []);

  return (
    <EvaluationDrawer
      id={project}
      listComponent={
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            height: "100%",
            position: "relative",
            width: {
              xs: "100%",
              sm: "100%",
              md: "590px",
            },
          }}
          role="presentation"
        >
          <Box
            display={"flex"}
            flexDirection={"row"}
            justifyContent={"space-between"}
          >
            <Box
              sx={{
                position: "sticky",
                top: 0,
                zIndex: 2,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                width: "92%",
              }}
            >
              <Typography
                typography="m3"
                color="text.primary"
                component="div"
                fontWeight={"fontWeightSemiBold"}
              >
                New Task
              </Typography>
              <Link
                href="https://docs.futureagi.com/docs/observe/features/evals"
                underline="always"
                color="blue.500"
                target="_blank"
                rel="noopener noreferrer"
                fontWeight="fontWeightMedium"
              >
                Learn more
              </Link>
            </Box>
            <IconButton
              onClick={handleClose}
              sx={{
                padding: 0,
              }}
            >
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Box>
          <Box
            sx={{
              height: "100%",
              maxHeight: "100vh",
              overflowY: "auto",
            }}
          >
            <form
              onSubmit={handleSubmit(onSubmit)}
              style={{
                display: "flex",
                flexDirection: "column",
                height: "91vh",
                gap: 2,
              }}
            >
              <Box
                sx={{
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 1.5,
                  flex: 1,
                  paddingY: 0.5,
                }}
              >
                {/* Text Field */}
                <FormTextFieldV2
                  control={control}
                  fieldName="name"
                  size="small"
                  label="Name"
                  variant="outlined"
                  fullWidth
                  autoFocus
                  placeholder="Enter task name"
                  required={true}
                  helperText={undefined}
                  defaultValue={undefined}
                  onBlur={undefined}
                />

                {observeId ? (
                  <TextField
                    size="small"
                    label="Project"
                    variant="outlined"
                    fullWidth
                    value={projectDetails?.result?.name}
                    placeholder="Choose Project"
                    disabled
                    InputLabelProps={{
                      shrink: true,
                    }}
                  />
                ) : (
                  <FormSearchSelectFieldControl
                    control={control}
                    fieldName="project"
                    size="small"
                    label="Project"
                    required={true}
                    options={
                      projectsList?.map((project) => ({
                        label: project.name,
                        value: project.id,
                      })) || []
                    }
                    style={{ width: "100%" }}
                    createLabel={
                      projectsList?.length > 0 ? null : "Add New project"
                    }
                    handleCreateLabel={() => navigate("/dashboard/observe")}
                    noOptions="No projects has been added"
                  />
                )}
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
                <Accordion defaultExpanded>
                  <AccordionSummary>Scheduled Run</AccordionSummary>
                  <AccordionDetails>
                    <ScheduledRuns control={control} />
                  </AccordionDetails>
                </Accordion>
                <Accordion defaultExpanded>
                  <AccordionSummary>Evaluations</AccordionSummary>
                  <AccordionDetails>
                    <EvaluationSection
                      selected={isProjectSelected}
                      savedEvals={mappedConfiguredEvals}
                      isEvalsLoading={isLoading}
                      isProjectEvals={true}
                      disabledMessage="To access the Evaluation section, please select a project first."
                      onRemoveEval={removeEval}
                    />
                    <FormHelperText
                      sx={{ paddingLeft: 1, marginTop: 0 }}
                      error={!!evalsDetailsErrorMessage}
                    >
                      {evalsDetailsErrorMessage}
                    </FormHelperText>
                  </AccordionDetails>
                </Accordion>
              </Box>
              <Box
                sx={{
                  mt: 1,
                  ml: "auto",
                }}
              >
                <Button
                  type="submit"
                  variant="contained"
                  color="primary"
                  sx={{ flex: 1, width: "200px" }}
                >
                  Save Task
                </Button>
              </Box>
            </form>
          </Box>
        </Box>
      }
      open={open}
      onClose={handleClose}
      allColumns={formattedEvalAttributes}
      refreshGrid={refreshGrid}
      module="task"
      onSuccess={(data, variables) => {
        if (Array.isArray(variables) && variables.length > 0) {
          variables.forEach((variable) => {
            if (!configuredEvals.some((e) => e.id === variable.id)) {
              addEval(variable);
            }
          });
        } else {
          addEval({
            ...variables,
            id: data?.data?.result?.id,
          });
        }
      }}
      showAdd={false}
      showTest={false}
      runLabel="Save"
    />
  );
};

NewTaskDrawerChild.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectDetails: PropTypes.object,
  refreshGrid: PropTypes.func,
  observeId: PropTypes.string,
};

const NewTaskDrawer = (props) => {
  return <NewTaskDrawerChild {...props} />;
};

NewTaskDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectDetails: PropTypes.any,
  refreshGrid: PropTypes.func,
  observeId: PropTypes.string,
};

export default NewTaskDrawer;
