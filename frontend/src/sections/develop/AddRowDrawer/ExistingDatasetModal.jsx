import {
  Box,
  Button,
  ClickAwayListener,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormHelperText,
  Grid,
  IconButton,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { existingDatasetValidationSchema } from "./validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  useGetDatasetColumns,
  useDevelopDatasetList,
} from "src/api/develop/develop-detail";
import { useNavigate, useParams } from "react-router";
import { ShowComponent } from "src/components/show";
import FieldSelection from "src/sections/develop-detail/Common/FieldSelection";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import RadioField from "src/components/RadioField/RadioField";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import { ConfirmDialog } from "src/components/custom-dialog";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { getExistingDatasetConfigValues } from "./existingDatasetMapping";
import { getCreatedDatasetCopyId } from "./existingDatasetResponse";

function replaceKeys(obj, arr) {
  const output = {};
  arr.forEach((item) => {
    const keyInObj = item.name;
    if (obj[keyInObj] !== undefined && obj[keyInObj] !== "") {
      output[item.id] = obj[keyInObj];
    }
  });
  return output;
}

const FieldSelectionSkeleton = (check) => {
  const theme = useTheme();
  return (
    <>
      {Array.from({ length: 8 }).map((_, index) => (
        <Grid
          container
          alignItems="center"
          spacing={theme.spacing(1)}
          key={index}
          sx={{ mb: theme.spacing(1) }}
        >
          {check && (
            <Grid item xs={12} sm={1}>
              <Skeleton variant="circular" width={24} height={24} />
            </Grid>
          )}

          <Grid item xs={12} sm={check ? 4 : 5}>
            <Skeleton variant="rectangular" height={40} />
          </Grid>

          <Grid item xs={12} sm={2} sx={{ textAlign: "center" }}>
            <Box
              display="flex"
              alignItems="center"
              sx={{ px: theme.spacing(1) }}
            >
              <Box
                sx={{
                  width: 8,
                  height: 8,
                  bgcolor: theme.palette.divider,
                  borderRadius: "50%",
                }}
              />
              <Box
                sx={{
                  flexGrow: 1,
                  height: "1px",
                  bgcolor: theme.palette.divider,
                  position: "relative",
                }}
              >
                <Box
                  sx={{
                    position: "absolute",
                    right: 0,
                    top: "50%",
                    transform: "translateY(-50%)",
                    width: 0,
                    height: 0,
                    borderTop: "4px solid transparent",
                    borderBottom: "4px solid transparent",
                    borderLeft: `6px solid ${theme.palette.divider}`,
                  }}
                />
              </Box>
            </Box>
          </Grid>

          <Grid item xs={12} sm={5}>
            <Skeleton variant="rectangular" height={40} />
          </Grid>
        </Grid>
      ))}
    </>
  );
};

FieldSelectionSkeleton.propTypes = {
  check: PropTypes.bool,
};

/**
 * !IMPORTANT
 * This component is reused in multiple places like
 * - AddRowDrawer
 * - AddRowDrawer in simulate
 * if you make any change here please make sure you test in both places
 */

const ExistingDatasetModal = ({
  open,
  onClose,
  refreshGrid,
  datasetId,
  ...rest
}) => {
  const { dataset: datasetFromParams } = useParams();
  const [newColumnData, setNewColumnsData] = useState([]);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [checkboxHandle, setCheckboxHandle] = useState({});
  const [openCreateColumn, setOpenCreateColumn] = useState("");
  const [isFullyOpen, setIsFullyOpen] = useState(false);
  const [selectedDatasetColumns, setSelectedDatasetColumns] = useState([]);
  const dataset = datasetId || datasetFromParams;

  const validationSchema = useMemo(
    () => existingDatasetValidationSchema(dataset, checkboxHandle),
    [dataset, checkboxHandle],
  );

  const theme = useTheme();
  const navigate = useNavigate();

  const {
    control,
    handleSubmit,
    reset,
    watch,
    setValue,
    trigger,
    formState: { errors, isDirty },
  } = useForm({
    defaultValues: {
      name: "",
      value: "",
      dataType: "ImportDataPromptConfiguration",
      num_rows: "",
      config: {
        mapping: {},
      },
    },
    resolver: zodResolver(validationSchema),
    mode: "onChange",
    reValidateMode: "onChange",
  });

  const {
    control: fields,
    handleSubmit: handleFormSubmit,
    reset: resetField,
  } = useForm({
    defaultValues: {
      name: "",
    },
  });

  const handleCheckbox = (e, name) => {
    const value = e.target.checked;
    trigger("config.mapping");

    setCheckboxHandle((prev) => ({
      ...prev,
      [name]: value,
    }));

    const updated = [...selectedDatasetColumns].map((item) =>
      item.name === name ? { ...item, checked: value } : item,
    );

    updated.sort((a, b) => {
      if (a.checked === b.checked) return 0;
      return a.checked ? -1 : 1;
    });

    setSelectedDatasetColumns(updated);
  };

  const selectedValue = watch("value");
  const configData = watch("config");
  const dataType = watch("dataType");

  const { data: datasetList } = useDevelopDatasetList(
    "",
    "",
    {},
    { include_experiments: true },
  );

  const { data: currentDatasetColumns } = useGetDatasetColumns(dataset, {
    enabled: !!dataset,
  });

  const { data: selectedDatasetColumn, isLoading: isSelectedDatasetColumn } =
    useGetDatasetColumns(
      selectedValue,
      { enabled: !!selectedValue && !!dataType },
      { include_prompt: dataType === "ImportDataPromptConfiguration" },
    );

  useEffect(() => {
    setSelectedDatasetColumns([]);
    if (selectedDatasetColumn?.length > 0) {
      setValue(
        "config",
        getExistingDatasetConfigValues(selectedDatasetColumn, {
          isAddingToExistingDataset: Boolean(dataset),
          targetColumns: currentDatasetColumns || [],
        }),
      );

      const updatedWithCheckbox = selectedDatasetColumn.map((col) => ({
        ...col,
        checked: true,
      }));

      setSelectedDatasetColumns(updatedWithCheckbox);

      const checkboxState = updatedWithCheckbox.reduce((acc, col) => {
        acc[col.name] = true;
        return acc;
      }, {});

      setCheckboxHandle(checkboxState);
    }
  }, [currentDatasetColumns, dataset, selectedDatasetColumn, setValue]);

  const performClose = () => {
    onClose();
    reset();
    setCheckboxHandle({});
  };

  const closeDrawer = (skipConfirm = false) => {
    if (!skipConfirm && isDirty) {
      setIsDialogOpen(true);
    } else {
      performClose();
    }
  };

  const handleConfirmClose = (confirm) => {
    if (confirm) {
      performClose();
    }
    setIsDialogOpen(false);
  };

  const { mutate: createColumn, isPending: createColumnLoading } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.addDatasetColumn(dataset), data),
    onSuccess: (data) => {
      const createdColumn = data?.data?.result?.data[0];
      setValue("config", {
        ...configData,
        mapping: {
          ...configData.mapping,
          [openCreateColumn]: createdColumn?.id,
        },
      });
      setNewColumnsData((pre) => [...pre, createdColumn]);
      enqueueSnackbar("Successfully Created New Column", {
        variant: "success",
      });
      setOpenCreateColumn("");
      resetField();
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create new column", {
          retryAction: "creating this column",
        }),
        { variant: "error" },
      );
    },
  });

  const { mutate: submitDataset, isPending: submitDatasetLoading } =
    useMutation({
      mutationFn: (data) =>
        dataset
          ? axios.post(
              endpoints.develop.addRowFromExistingDataset(dataset),
              data,
            )
          : axios.post(endpoints.develop.createFromExistingDataset, data),
      onSuccess: (data, variables) => {
        trackEvent(Events.addRowsSuccess, {
          [PropertyName.method]:
            "add from existing model dataset or experiment",
        });

        enqueueSnackbar(
          dataset
            ? "Rows imported successfully"
            : "New dataset has been created",
          { variant: "success" },
        );

        performClose();
        refreshGrid({ purge: true }, true);
        rest?.closeDrawer();

        const datasetName = datasetList?.find(
          (item) => item.datasetId === dataset,
        )?.name;

        trackEvent(Events.datasetFromExistingDatasetCreated, {
          [PropertyName.method]: dataType,
          [PropertyName.column]: variables?.column_mapping,
          [PropertyName.name]: datasetName,
        });

        const createdDatasetId = getCreatedDatasetCopyId(data);
        if (!dataset && createdDatasetId) {
          navigate(`/dashboard/develop/${createdDatasetId}?tab=data`);
        }
      },
      onError: (error) => {
        enqueueSnackbar(
          getRequestErrorMessage(error, "Failed to add dataset", {
            retryAction: "adding this dataset",
          }),
          { variant: "error" },
        );
      },
    });

  const onSubmit = (formData) => {
    let payload = {};
    const output = replaceKeys(
      configData?.mapping || {},
      selectedDatasetColumns,
    );

    if (dataset) {
      payload = {
        source_dataset_id: selectedValue,
        column_mapping: output,
        ...(formData.num_rows !== "" && formData.num_rows != null
          ? { num_rows: Number(formData.num_rows) }
          : {}),
      };
    } else {
      const columns = {};
      const outputcheckbox = replaceKeys(
        checkboxHandle || {},
        selectedDatasetColumns,
      );
      Object.entries(outputcheckbox).forEach(([key, value]) => {
        if (value) {
          columns[key] = output[key];
        }
      });
      payload = {
        name: formData.name,
        dataset_id: selectedValue,
        columns,
      };
    }

    if (payload?.columns && !Object.keys(payload?.columns)?.length) {
      return enqueueSnackbar("Please select one column at least", {
        variant: "error",
      });
    }
    submitDataset(payload);
  };

  const onSubmitCreateColumn = (formData) => {
    const payload = {
      new_columns_data: [
        {
          name: formData.name,
          data_type: "text",
        },
      ],
    };
    createColumn(payload);
  };

  useEffect(() => {
    let timer;
    if (open) {
      timer = setTimeout(() => {
        setIsFullyOpen(true);
      }, 300);
    } else {
      setIsFullyOpen(false);
    }

    return () => clearTimeout(timer);
  }, [open]);

  const handleClickAway = () => {
    if (isFullyOpen) {
      if (isDirty) {
        setIsDialogOpen(true);
      } else {
        performClose();
      }
    }
  };

  return (
    <Collapse in={open} orientation="horizontal">
      <ClickAwayListener onClickAway={handleClickAway} mouseEvent="onMouseDown">
        <Box
          sx={{
            width: "550px",
            height: "100vh",
            borderRight: "1px solid",
            borderColor: "divider",
            overflowY: "auto",
            padding: theme.spacing(2),
          }}
        >
          <Box
            display={"flex"}
            flexDirection={"column"}
            gap={theme.spacing(0.25)}
          >
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <Box marginBottom={theme.spacing(0.5)}>
                <Typography
                  fontWeight={"fontWeightSemiBold"}
                  color="text.primary"
                  variant="m3"
                >
                  Add from existing model dataset or experiment
                </Typography>
              </Box>
              <IconButton
                onClick={() => closeDrawer()}
                sx={{ padding: (theme) => theme.spacing(0) }}
              >
                <Iconify icon="mingcute:close-line" color="text.primary" />
              </IconButton>
            </Box>
            <HelperText
              text={
                dataset
                  ? "Choose from existing datasets to import rows into this dataset"
                  : "Choose from the existing datasets in our system to create a new dataset"
              }
            />
          </Box>
          <React.Fragment>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                flex: 1,
              }}
              component="form"
              onSubmit={handleSubmit(onSubmit)}
            >
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: theme.spacing(2),
                  marginTop: theme.spacing(3.5),
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: theme.spacing(2),
                  }}
                >
                  {!dataset && (
                    <FormTextFieldV2
                      defaultValue={""}
                      helperText={""}
                      control={control}
                      fieldName="name"
                      label="Dataset Name"
                      size="small"
                      placeholder="Enter dataset Name"
                    />
                  )}
                  <FormSearchSelectFieldControl
                    isSearchable={true}
                    control={control}
                    options={datasetList
                      ?.filter((item) => item.datasetId !== dataset)
                      ?.map((item) => ({
                        label: item.name,
                        value: item.datasetId,
                      }))}
                    fieldName="value"
                    valueSelector={(o) => o.value}
                    fullWidth
                    label="Choose Datasets or experiments"
                    size="small"
                    dropDownMaxHeight={300}
                    error={""}
                    helperText={""}
                  />
                  {dataset && (
                    <FormTextFieldV2
                      control={control}
                      fieldName="num_rows"
                      size="small"
                      label="No. of rows"
                      variant="outlined"
                      fullWidth
                      isSpinnerField
                      fieldType="number"
                      placeholder="Enter number of rows (leave blank to import all)"
                    />
                  )}
                  <RadioField
                    control={control}
                    fieldName={"dataType"}
                    label={""}
                    optionDirection={"column"}
                    options={[
                      { label: "Import Data", value: "importData" },
                      {
                        label: "Import data and prompt configuration",
                        value: "ImportDataPromptConfiguration",
                      },
                    ]}
                    optionColor="text.primary"
                    groupSx={{
                      padding: (theme) => theme.spacing(0),
                      margin: (theme) => theme.spacing(0),
                      gap: (theme) => theme.spacing(1.5),
                      "& .MuiRadio-root": {
                        padding: (theme) => theme.spacing(0),
                        margin: (theme) => theme.spacing(0),
                        marginRight: (theme) => theme.spacing(1.5),
                      },
                      "& .MuiFormControlLabel-root": {
                        margin: (theme) => theme.spacing(0),
                      },
                    }}
                    optionSx={{
                      "& .Mui-checked + .MuiTypography-root": {
                        fontWeight: "fontWeightMedium",
                      },
                    }}
                  />
                </Box>
                <Box
                  display={"flex"}
                  flexDirection={"column"}
                  gap={theme.spacing(1.5)}
                >
                  <Typography
                    fontWeight={"fontWeightMedium"}
                    color="text.primary"
                    variant="s1"
                  >
                    {dataset ? "Map to existing dataset" : "Map to new dataset"}
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: theme.spacing(1),
                      padding: theme.spacing(2),
                      overflow: "auto",
                      height: `calc(100vh - ${dataset ? "330" : "380"}px)`,
                    }}
                  >
                    <ShowComponent
                      condition={
                        dataType &&
                        configData?.mapping &&
                        Object.keys(configData?.mapping).length > 0 &&
                        selectedDatasetColumns?.length > 0
                      }
                    >
                      <Box
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          gap: theme.spacing(1.5),
                        }}
                      >
                        {isSelectedDatasetColumn ? (
                          <FieldSelectionSkeleton check={true} />
                        ) : (
                          configData?.mapping &&
                          Object.keys(configData.mapping).length > 0 &&
                          selectedDatasetColumns?.length > 0 &&
                          selectedDatasetColumns.map((field) => {
                            let allColumns = [];

                            const arr =
                              (dataset
                                ? currentDatasetColumns
                                : selectedDatasetColumns) || [];

                            allColumns = arr.map((item) => ({
                              headerName: item.name,
                              field: item.id,
                            }));

                            if (newColumnData) {
                              newColumnData.forEach((item) => {
                                allColumns.push({
                                  headerName: item.name,
                                  field: item.id,
                                });
                              });
                            }

                            allColumns = Array.from(
                              new Map(
                                allColumns.map((item) => [item.field, item]),
                              ).values(),
                            );

                            if (allColumns.length === 0) return null;

                            const textValue = field.name;

                            return (
                              <FieldSelection
                                key={field.id}
                                field={textValue}
                                allColumns={allColumns}
                                control={control}
                                isMultipleColumn={false}
                                isSearchable={true}
                                createLabel={
                                  dataset ? "Create New Column" : null
                                }
                                handleCreateLabel={() =>
                                  setOpenCreateColumn(textValue)
                                }
                                contentWidth={true}
                                dropDownMaxHeight={200}
                                check={!dataset}
                                handleCheckbox={(e) =>
                                  handleCheckbox(e, textValue)
                                }
                                isChecked={checkboxHandle?.[field.name]}
                              />
                            );
                          })
                        )}
                      </Box>
                    </ShowComponent>
                    <ShowComponent condition={!selectedDatasetColumns?.length}>
                      <Box
                        sx={{
                          display: "flex",
                          height: `calc(100vh - ${dataset ? "380" : "430"}px)`,
                          justifyContent: "center",
                          alignItems: "center",
                        }}
                      >
                        <Typography
                          variant="s1"
                          fontWeight="fontWeightRegular"
                          color="text.primary"
                        >
                          Choose Dataset
                        </Typography>
                      </Box>
                    </ShowComponent>
                    {errors?.config?.mapping?.root?.message && (
                      <FormHelperText
                        sx={{
                          marginTop: theme.spacing(0),
                          marginLeft: theme.spacing(0),
                        }}
                        error={true}
                      >
                        {errors?.config?.mapping?.root?.message}
                      </FormHelperText>
                    )}
                  </Box>
                </Box>
              </Box>
              <Box
                sx={{
                  display: "flex",
                  gap: theme.spacing(1.5),
                  justifyContent: "flex-end",
                  marginTop: theme.spacing(2),
                }}
              >
                <LoadingButton
                  onClick={() => closeDrawer()}
                  // size="small"
                  fullWidth
                  variant="outlined"
                  sx={{
                    typography: "s1",
                    fontWeight: "fontWeightMedium",
                    paddingY: (theme) => theme.spacing(1),
                    paddingX: (theme) => theme.spacing(3),
                  }}
                >
                  Cancel
                </LoadingButton>
                <LoadingButton
                  type="submit"
                  fullWidth
                  // size="small"
                  variant="contained"
                  color="primary"
                  loading={submitDatasetLoading}
                  sx={{
                    typography: "s1",
                    fontWeight: "fontWeightMedium",
                    paddingY: (theme) => theme.spacing(1),
                    paddingX: (theme) => theme.spacing(3),
                  }}
                >
                  Add
                </LoadingButton>
              </Box>
            </Box>

            <Dialog
              open={Boolean(openCreateColumn)}
              onClose={() => setOpenCreateColumn("")}
              maxWidth="sm"
              fullWidth
            >
              <DialogTitle
                sx={{
                  padding: theme.spacing(2),
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <Typography fontWeight="fontWeightBold" variant="m2">
                  Create New Column
                </Typography>
                <IconButton onClick={() => setOpenCreateColumn("")}>
                  <Iconify icon="mdi:close" />
                </IconButton>
              </DialogTitle>
              <form onSubmit={handleFormSubmit(onSubmitCreateColumn)}>
                <DialogContent
                  sx={{
                    paddingTop: 0.5,
                  }}
                >
                  <FormTextFieldV2
                    autoFocus
                    label="Enter Column Name"
                    size="small"
                    placeholder="Enter column name"
                    control={fields}
                    fieldName="name"
                    fullWidth
                    required
                  />
                </DialogContent>
                <DialogActions sx={{ padding: theme.spacing(2) }}>
                  <Button
                    onClick={() => setOpenCreateColumn("")}
                    variant="outlined"
                  >
                    Cancel
                  </Button>
                  <LoadingButton
                    variant="contained"
                    color="primary"
                    loading={createColumnLoading}
                    type="submit"
                  >
                    Save
                  </LoadingButton>
                </DialogActions>
              </form>
            </Dialog>

            <ConfirmDialog
              open={isDialogOpen}
              title="Confirm Action"
              message="Are you sure you want to close?"
              content="Your unsaved changes will be lost. Do you still want to close?"
              onClose={() => handleConfirmClose(false)}
              action={
                <Button
                  size="small"
                  variant="contained"
                  color="error"
                  onClick={() => handleConfirmClose(true)}
                >
                  Confirm
                </Button>
              }
            />
          </React.Fragment>
        </Box>
      </ClickAwayListener>
    </Collapse>
  );
};

ExistingDatasetModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
};

export default ExistingDatasetModal;
