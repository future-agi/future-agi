import React, { useEffect, useMemo, useState } from "react";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import _ from "lodash";
import {
  Box,
  Button,
  Collapse,
  FormHelperText,
  IconButton,
  Tab,
  Tabs,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSelectField } from "src/components/FormSelectField";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import NewTaskFilterBox from "../NewTaskDrawer/NewTaskFilterBox";
import ScheduledRuns from "../NewTaskDrawer/ScheduledRuns";
import PropTypes from "prop-types";
import { useGetProjectById } from "src/api/project/evals-task";
import {
  useController,
  useFieldArray,
  useForm,
  useFormState,
  useWatch,
} from "react-hook-form";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import {
  extractAttributeFilters,
  getNewTaskFilters,
  NewTaskValidationSchema,
} from "../NewTaskDrawer/validation";
import ConfiguredEvaluationType from "src/sections/develop-detail/Common/ConfiguredEvaluationType/ConfiguredEvaluationType";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { zodResolver } from "@hookform/resolvers/zod";
import { getDefaultTaskValues } from "../common";
import TaskConfirmDialog from "./TaskConfirmBox";
import TaskLogs from "./TaskLogs";
import { ShowComponent } from "src/components/show";
import { useEvaluationContext } from "../../EvaluationDrawer/context/EvaluationContext";
import EvaluationSection from "../NewTaskDrawer/EvaluationSection";
import { red } from "src/theme/palette";
import FilterErrorBoundary from "src/components/ComplexFilter/FilterErrorBoundary";
import EvaluationDrawer from "../../EvaluationDrawer/EvaluationDrawer";
import { objectCamelToSnake } from "src/utils/utils";
import { resetEvalStore } from "src/sections/evals/store/useEvalStore";

function CustomTabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && <Box>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  index: PropTypes.number.isRequired,
  value: PropTypes.number.isRequired,
};

const TabOptions = [
  { label: "Details", value: "Details", disabled: false },
  { label: "Logs", value: "Logs", disabled: false },
];

const DetailsEdit = ({
  loading,
  selectedRow,
  title,
  isEdit,
  observeId,
  taskDetails,
  onClose,
  refreshGrid,
  isView,
  open,
}) => {
  const { role } = useAuthContext();
  const [isAlert, setIsAlert] = useState(false);
  const [selectedTab, setSelectedTab] = useState("Details");
  const theme = useTheme();
  const { control, handleSubmit, getValues, setValue } = useForm({
    defaultValues: getDefaultTaskValues(taskDetails, observeId),
    resolver: zodResolver(NewTaskValidationSchema()),
  });

  const { data: projectDetails } = useGetProjectById(observeId, {
    enabled: !!observeId,
  });

  const project = useWatch({ control, name: "project" });
  const isProjectSelected = !!project;
  const [configureEvalOpen, setConfigureEvalOpen] = useState(false);
  const [, setSelectedEval] = useState(null);

  const { field: startDateField } = useController({
    control,
    name: "startDate",
  });
  const { field: endDateField } = useController({ control, name: "endDate" });

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

  const queryClient = useQueryClient();

  const evalsDetailsErrorMessage = _.get(errors, "evalsDetails")?.message || "";
  const { visibleSection } = useEvaluationContext();
  const formValues = useWatch({ control });

  const _filters = useMemo(() => {
    return getNewTaskFilters(formValues, project, true).filters || {};
  }, [formValues.filters]);

  const filters = useDebounce(_filters, 500);

  const { mutate: createEvalTask } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.createEvalTask(), {
        ...data,
      }),
    onSuccess: () => {
      enqueueSnackbar("Eval Task Created Successfully", { variant: "success" });
      refreshGrid();
      onClose();
    },
  });

  const onAddSubmit = (data) => {
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

  const { data: configuredEvalList, isLoading } = useQuery({
    queryKey: ["configured-evals", project, filters],
    queryFn: () =>
      axios.get(endpoints.project.getEvalTaskConfig(), {
        params: {
          project_id: project,
          filters: JSON.stringify(objectCamelToSnake(filters)),
          task_id: selectedRow?.id,
        },
      }),
    select: (data) => data.data?.result,
    enabled: !!project,
  });

  useEffect(() => {
    if (!configuredEvalList) return;

    replace(configuredEvalList);
  }, [configuredEvalList]);

  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", filters],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          filters: JSON.stringify(objectCamelToSnake(filters)),
        },
      }),
    select: (data) => data.data?.result,
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

  const { mutate: updateEvalTask, isPending } = useMutation({
    mutationFn: (data) =>
      axios.patch(endpoints.project.patchEvalTask(), {
        ...data,
        eval_task_id: selectedRow?.id,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: ["taskDetails", taskDetails?.id],
      });
      enqueueSnackbar(data?.data?.result?.message, { variant: "success" });
      refreshGrid();
      onClose();
    },
  });

  const onUpdateSubmit = (data, editType) => {
    const attributeFilters = extractAttributeFilters(data?.filters);

    const observationTypes = data.filters
      .filter((f) => f.property === "observationType")
      .map((f) => f?.filterConfig?.filterValue);

    const transformedData = {
      evals: data.evalsDetails?.map((item) => item.id) || [],
      filters: {
        project_id: data.project,
        date_range: [
          new Date(startDateField.value).toISOString(),
          new Date(endDateField.value).toISOString(),
        ],
        ...(observationTypes && observationTypes?.length > 0
          ? { observation_type: observationTypes }
          : {}),
        ...(attributeFilters && attributeFilters?.length > 0
          ? { span_attributes_filters: attributeFilters }
          : {}),
      },
      observation_type: observationTypes,
      project_id: data.project,
      name: data.name,
      project: data.project,
      run_type: data.runType,
      // row_type intentionally omitted from update payload — immutable
      // after task creation; the BE serializer rejects it on PATCH.
      sampling_rate: data.samplingRate,
      spans_limit: String(data.spansLimit),
      edit_type: editType,
    };
    updateEvalTask(transformedData);
  };

  const onSubmit = (data) => {
    if (!isEdit) {
      return onAddSubmit(data);
    }

    if (isView && !isAlert) {
      return setIsAlert(true);
    }
  };
  const handleConfirmWithEditType = (editType) => {
    const formData = formValues;
    onUpdateSubmit(formData, editType);
  };

  const { mutate: createEvalTaskConfig } = useMutation({
    /**
     *
     * @param {Object} d
     * @returns
     */
    mutationFn: (d) => {
      return axios.post(endpoints.project.createEvalTaskConfig(), d);
    },
    onSuccess: (data, variables) => {
      addEval({
        ...variables,
        id: data?.data?.result?.id,
      });
    },
  });

  const { mutate: handleApplyGroup } = useMutation({
    mutationFn: async (payload) => {
      return axios.post(endpoints.develop.eval.applyEvalGroup, payload);
    },
    onSuccess: (data) => {
      if (Array.isArray(data?.data?.result) && data?.data?.result.length > 0) {
        data.data.result?.forEach((variable) => {
          if (!configuredEvals.some((e) => e.id === variable.id)) {
            addEval(variable);
          }
        });
      }
      resetEvalStore();
    },
  });

  const mappedConfiguredEvals = configuredEvals.map((item) => ({
    ...item,
    evalRequiredKeys: item?.mapping ? Object.keys(item.mapping) : [],
  }));

  return (
    <EvaluationDrawer
      id={observeId}
      open={open}
      onClose={onClose}
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
      type="temporary"
      handleSaveAndRun={(values, _, { isGrouped = false } = {}) => {
        if (isGrouped) {
          handleApplyGroup(values);
        } else {
          createEvalTaskConfig({
            project: project,
            name: values.name,
            eval_template: values.eval_template,
            mapping: values.mapping,
            config: values.config,
            filters: getNewTaskFilters(formValues, observeId, true).filters,
          });
        }
      }}
      listComponent={
        <Box display="flex" flexDirection="row" height="100%">
          <Collapse
            in={visibleSection === "list"}
            unmountOnExit
            orientation="horizontal"
          >
            <Collapse
              in={configureEvalOpen}
              orientation="horizontal"
              unmountOnExit
            >
              <ConfiguredEvaluationType
                onClose={() => {
                  setConfigureEvalOpen(false);
                }}
                onOptionClick={(selectedEval) => {
                  setConfigureEvalOpen(false);
                  setSelectedEval({
                    ...selectedEval,
                    previouslyConfigured: true,
                  });
                }}
                datasetId={observeId}
              />
            </Collapse>

            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                bgcolor: "background.paper",
                height: "100%",
                position: "relative",
                width: {
                  xs: "100%",
                  // sm: "45%",
                  md: "640px",
                },
                minWidth: 0,
                zIndex: 10,
              }}
              role="presentation"
            >
              <IconButton
                onClick={onClose}
                sx={{
                  position: "absolute",
                  top: 4,
                  right: 10,
                  zIndex: 3,
                }}
              >
                <Iconify icon="mingcute:close-line" />
              </IconButton>
              <Box
                sx={{
                  position: "sticky",
                  top: 0,
                  zIndex: 2,
                }}
              >
                <Typography variant="h6" color="text.primary" component="div">
                  {title}
                </Typography>
              </Box>

              <ShowComponent condition={isView}>
                <Box
                  sx={{
                    borderBottom: 1,
                    borderColor: "divider",
                    mt: 1.5,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <Tabs
                    textColor="primary"
                    value={selectedTab}
                    onChange={(e, value) => {
                      setSelectedTab(value);
                    }}
                    TabIndicatorProps={{
                      style: {
                        backgroundColor: theme.palette.primary.main,
                      },
                    }}
                    sx={{
                      minHeight: 0,
                      "& .MuiTab-root": {
                        margin: "0 !important",
                        fontWeight: "600",
                        color: "primary.main",
                        "&:not(.Mui-selected)": {
                          color: "text.disabled",
                          fontWeight: "500",
                        },
                      },
                    }}
                  >
                    {TabOptions.map((tab) => (
                      <Tab
                        key={tab.value}
                        label={tab.label}
                        value={tab.value}
                        disabled={tab.disabled}
                        sx={{
                          margin: theme.spacing(0),
                          px: theme.spacing(1.875),
                        }}
                      />
                    ))}
                  </Tabs>
                </Box>
              </ShowComponent>

              <ShowComponent condition={selectedTab === "Details"}>
                <Box
                  sx={{
                    // height: "100%",
                    height: "100vh",
                    overflowY: "auto",
                  }}
                >
                  <form
                    onSubmit={handleSubmit(onSubmit)}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      height: "100%",
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
                        paddingY: 2,
                      }}
                    >
                      {/* Text Field */}
                      <FormTextFieldV2
                        control={control}
                        fieldName="name"
                        size="small"
                        label="Name"
                        placeholder="Enter name"
                        defaultValue={selectedRow?.name}
                        variant="outlined"
                        fullWidth
                        autoFocus={!loading}
                        required={true}
                        helperText={undefined}
                        onBlur={undefined}
                      />

                      {observeId ? (
                        <TextField
                          size="small"
                          label="Project"
                          variant="outlined"
                          fullWidth
                          value={
                            projectDetails?.name ??
                            projectDetails?.result?.name ??
                            ""
                          }
                          disabled
                          required
                          sx={{
                            "& .MuiInputBase-root": { paddingRight: "60px" },
                            "& .MuiFormLabel-asterisk": { color: red[500] },
                          }}
                        />
                      ) : (
                        <FormSelectField
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
                        />
                      )}
                      <FilterErrorBoundary>
                        <Accordion defaultExpanded>
                          <AccordionSummary>
                            <Typography
                              sx={{
                                fontSize: "14px",
                                color: "text.primary",
                                fontWeight: 500,
                              }}
                            >
                              Filters
                            </Typography>
                          </AccordionSummary>
                          <AccordionDetails>
                            <NewTaskFilterBox
                              getValues={getValues}
                              setValue={setValue}
                              attributes={
                                Array.isArray(evalAttributes)
                                  ? evalAttributes.map((attr) => ({
                                      label: attr,
                                      value: attr,
                                    }))
                                  : []
                              }
                              control={control}
                            />
                          </AccordionDetails>
                        </Accordion>
                      </FilterErrorBoundary>
                      <Accordion defaultExpanded>
                        <AccordionSummary>
                          <Typography
                            sx={{
                              fontSize: "14px",
                              color: "text.primary",
                              fontWeight: 500,
                            }}
                          >
                            Scheduled Run
                          </Typography>
                        </AccordionSummary>
                        <AccordionDetails>
                          <ScheduledRuns
                            control={control}
                            dayLimit={"Custom"}
                            isEdit={true}
                          />
                        </AccordionDetails>
                      </Accordion>
                      <Accordion defaultExpanded>
                        <AccordionSummary>
                          <Typography
                            sx={{
                              fontSize: "14px",
                              color: "text.primary",
                              fontWeight: 500,
                            }}
                          >
                            Evaluations
                          </Typography>
                        </AccordionSummary>
                        <AccordionDetails>
                          <EvaluationSection
                            selected={isProjectSelected}
                            savedEvals={mappedConfiguredEvals}
                            isEvalsLoading={isLoading}
                            onRemoveEval={removeEval}
                            disabledMessage={
                              "To access the Evaluation section, please select a project first."
                            }
                            isProjectEvals={true}
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
                        paddingBottom: "5px",
                        ml: "auto",
                      }}
                    >
                      <Button
                        type="submit"
                        variant="contained"
                        color="primary"
                        sx={{ flex: 1, width: "200px" }}
                        disabled={
                          !RolePermission.OBSERVABILITY[
                            PERMISSIONS.ADD_TASKS_ALERTS
                          ][role]
                        }
                      >
                        {isEdit ? `Update Task` : `Save Task`}
                      </Button>
                    </Box>
                  </form>
                </Box>
              </ShowComponent>

              <ShowComponent condition={selectedTab === "Logs"}>
                <TaskLogs evalTaskId={selectedRow?.id} />
              </ShowComponent>

              <TaskConfirmDialog
                content="Select one of the options"
                onConfirm={handleConfirmWithEditType}
                open={isAlert}
                isLoading={isPending}
                onClose={() => setIsAlert(false)}
                title="Run this Task"
                message="Are you sure you want to proceed? This action can cause your data loss."
              />
            </Box>
          </Collapse>
        </Box>
      }
    />
  );
};

DetailsEdit.propTypes = {
  loading: PropTypes.bool,
  title: PropTypes.string,
  handleSubmit: PropTypes.func,
  onSubmit: PropTypes.any,
  control: PropTypes.any,
  createEvalTaskConfig: PropTypes.func,
  formattedEvalAttributes: PropTypes.any,
  selectedRow: PropTypes.object,
  projectsList: PropTypes.array,
  setEvaluationTypeOpen: PropTypes.func,
  setConfigureEvalOpen: PropTypes.func,
  setSelectedEval: PropTypes.func,
  removeEval: PropTypes.func,
  configuredEvals: PropTypes.any,
  evalsDetailsErrorMessage: PropTypes.any,
  isEdit: PropTypes.bool,
  isView: PropTypes.bool,
  observeId: PropTypes.string,
  projectDetails: PropTypes.array,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  taskDetails: PropTypes.object,
  open: PropTypes.bool,
};

export default DetailsEdit;
